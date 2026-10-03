"""
Device Polling Tasks API
Управление задачами фонового опроса энергетических устройств
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select
from typing import List
from loguru import logger

from backend.database.db import get_db
from backend.database.models import AccessLevel
from backend.database.models.energy import (
    DevicePollingTask,
    EnergeticDevice,
    EnergeticObject,
    WebSocketBroadcastTask,
)
from backend.database.redis_db import signal_worker_reload
from backend.database.models.user import User
from backend.repository.energy.energetic_device import get_device_access
from backend.repository.energy.energetic_object_access import (
    ensure_object_permission,
    has_required_access,
    is_admin_or_superadmin,
)
from backend.schemas.device_polling import (
    CombinedObjectPollingTasksResponse,
    DevicePollingTaskCreate,
    DevicePollingTaskListResponse,
    DevicePollingTaskResponse,
    DevicePollingTasksActivationUpdate,
    DevicePollingTasksBulkUpdateResponse,
    DevicePollingTaskUpdate,
)
from backend.schemas.websocket import WebSocketBroadcastTaskResponse
from backend.services.shared.access import admin_access, user_access
from backend.services.telegram.telegram_manager import telegram_manager
from backend.services.user.auth import auth_service


router = APIRouter(prefix="/polling-tasks", tags=["Device Polling Tasks"])


def _build_bulk_update_response(
    *,
    scope: str,
    scope_id: str,
    energetic_object_ids: List[str],
    is_active: bool,
    tasks: List[DevicePollingTask],
    updated_tasks: int,
) -> DevicePollingTasksBulkUpdateResponse:
    return DevicePollingTasksBulkUpdateResponse(
        scope=scope,
        scope_id=scope_id,
        energetic_object_ids=energetic_object_ids,
        is_active=is_active,
        total_tasks=len(tasks),
        updated_tasks=updated_tasks,
        active_tasks=sum(1 for task in tasks if task.is_active),
        tasks=[DevicePollingTaskResponse.model_validate(task) for task in tasks],
    )


async def _set_tasks_active(
    db: AsyncSession,
    tasks: List[DevicePollingTask],
    is_active: bool,
) -> int:
    updated_tasks = 0
    for task in tasks:
        if task.is_active != is_active:
            task.is_active = is_active
            updated_tasks += 1

    await db.commit()
    for task in tasks:
        await db.refresh(task)

    return updated_tasks


async def _resolve_object_bridge_session_aliases(
    db: AsyncSession,
    bridge_refs: List[str] | None,
) -> List[str]:
    refs = [str(ref).strip() for ref in (bridge_refs or []) if str(ref).strip()]
    if not refs:
        return []

    result = await db.execute(
        select(EnergeticDevice).where(
            or_(
                EnergeticDevice.id.in_(refs),
                EnergeticDevice.device_id.in_(refs),
            )
        )
    )
    devices = list(result.scalars().all())

    aliases = list(refs)
    for device in devices:
        aliases.extend(
            alias for alias in (device.device_id, str(device.id)) if alias
        )

    return list(dict.fromkeys(aliases))


@router.post(
    "/",
    response_model=DevicePollingTaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать задачу фонового опроса для обьекта",
    description="""
    Создаёт новую задачу фонового опроса для энергетического объекта.
    
    **Типы задач:**
    - `cerbo_collection` - Сбор данных с Cerbo GX через API
    - `schedule_check` - Проверка и применение расписания
    - `modbus_registers` - Чтение Modbus регистров по конфигу
    - `custom_command` - Пользовательская команда
    
    **Пример command_config для modbus_registers:**
    ```json
    {
        "register_groups": ["battery", "solar", "grid"]
    }
    ```
    """
)
async def create_polling_task(
    task: DevicePollingTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    """Создание новой задачи опроса"""
    try:
        # Проверяем существование объекта
        result = await db.execute(
            select(EnergeticObject).where(EnergeticObject.id == task.energetic_object_id)
        )
        energetic_object = result.scalar_one_or_none()
        
        if not energetic_object:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Energetic object {task.energetic_object_id} not found"
            )
        await ensure_object_permission(
            db,
            energetic_object_id=task.energetic_object_id,
            user=current_user,
            required_access=AccessLevel.READ_WRITE,
        )
        
        # Создаём задачу
        new_task = DevicePollingTask(
            energetic_object_id=task.energetic_object_id,
            task_type=task.task_type.value,
            command_config=task.command_config,
            interval_ms=task.interval_ms,
            is_active=task.is_active
        )
        
        db.add(new_task)
        await db.commit()
        await db.refresh(new_task)
        
        logger.info(f"Created polling task {new_task.id} for object {task.energetic_object_id}")
        await signal_worker_reload()
        return DevicePollingTaskResponse.model_validate(new_task)
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error creating polling task: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create polling task: {str(e)}"
        )


@router.get(
    "/object/{object_id}",
    response_model=DevicePollingTaskListResponse,
    summary="Получить все задачи объекта",
    description="Возвращает список всех задач опроса для указанного энергетического объекта"
)
async def get_object_polling_tasks(
    object_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    """Получение всех задач опроса для объекта"""
    try:
        # Получаем объект
        result = await db.execute(
            select(EnergeticObject).where(EnergeticObject.id == object_id)
        )
        energetic_object = result.scalar_one_or_none()
        
        if not energetic_object:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Energetic object {object_id} not found"
            )
        await ensure_object_permission(
            db,
            energetic_object_id=object_id,
            user=current_user,
            required_access=AccessLevel.READ,
        )
        
        # Получаем все задачи
        result = await db.execute(
            select(DevicePollingTask).where(
                DevicePollingTask.energetic_object_id == object_id
            ).order_by(DevicePollingTask.created_at.desc())
        )
        tasks = result.scalars().all()
        
        active_count = sum(1 for task in tasks if task.is_active)
        
        return DevicePollingTaskListResponse(
            energetic_object_id=object_id,
            energetic_object_name=energetic_object.name,
            tasks=[DevicePollingTaskResponse.model_validate(task) for task in tasks],
            total_tasks=len(tasks),
            active_tasks=active_count
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting polling tasks: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get polling tasks: {str(e)}"
        )


@router.get(
    "/object/{object_id}/combined",
    response_model=CombinedObjectPollingTasksResponse,
    summary="Получить задачи объекта и связанных COR Bridge",
    description=(
        "Возвращает обычные polling-задачи объекта и broadcast-задачи COR Bridge, "
        "которые привязаны к этому объекту через cor_bridges."
    ),
)
async def get_object_combined_polling_tasks(
    object_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    """Получение всех фоновых задач объекта и связанных COR Bridge."""
    try:
        energetic_object = await ensure_object_permission(
            db,
            energetic_object_id=object_id,
            user=current_user,
            required_access=AccessLevel.READ,
        )

        object_tasks_result = await db.execute(
            select(DevicePollingTask).where(
                DevicePollingTask.energetic_object_id == object_id
            ).order_by(DevicePollingTask.created_at.desc())
        )
        object_tasks = list(object_tasks_result.scalars().all())

        bridge_session_ids = await _resolve_object_bridge_session_aliases(
            db,
            list(energetic_object.cor_bridges or []),
        )
        if bridge_session_ids:
            bridge_tasks_result = await db.execute(
                select(WebSocketBroadcastTask).where(
                    WebSocketBroadcastTask.session_id.in_(bridge_session_ids)
                ).order_by(WebSocketBroadcastTask.created_at.desc())
            )
            bridge_tasks = list(bridge_tasks_result.scalars().all())
        else:
            bridge_tasks = []

        object_active_tasks = sum(1 for task in object_tasks if task.is_active)
        bridge_active_tasks = sum(1 for task in bridge_tasks if task.is_active)

        return CombinedObjectPollingTasksResponse(
            energetic_object_id=object_id,
            energetic_object_name=energetic_object.name,
            bridge_session_ids=bridge_session_ids,
            object_tasks=[
                DevicePollingTaskResponse.model_validate(task)
                for task in object_tasks
            ],
            bridge_tasks=[
                WebSocketBroadcastTaskResponse.model_validate(task)
                for task in bridge_tasks
            ],
            object_total_tasks=len(object_tasks),
            object_active_tasks=object_active_tasks,
            bridge_total_tasks=len(bridge_tasks),
            bridge_active_tasks=bridge_active_tasks,
            total_tasks=len(object_tasks) + len(bridge_tasks),
            active_tasks=object_active_tasks + bridge_active_tasks,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting combined polling tasks: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get combined polling tasks: {str(e)}"
        )


@router.get(
    "/tg_diag",
    status_code=status.HTTP_200_OK,
    summary="Telegram diagnostics",
    description="Возвращает диагностику обработки Telegram-команд: polling, фильтры, очереди, воркеры"
)
async def get_telegram_diagnostics(
    admin: User = Depends(admin_access)
):
    """Диагностика Telegram polling/commands pipeline."""
    try:
        return {
            "status": "success",
            "data": telegram_manager.get_diagnostics(),
        }
    except Exception as e:
        logger.error(f"Error getting Telegram diagnostics: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get Telegram diagnostics: {str(e)}"
        )


@router.patch(
    "/object/{object_id}/set-active",
    response_model=DevicePollingTasksBulkUpdateResponse,
    summary="Включить/выключить все задачи объекта",
    description="Устанавливает статус активности для всех задач фонового опроса энергетического объекта"
)
async def set_object_polling_tasks_active(
    object_id: str,
    payload: DevicePollingTasksActivationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    """Массовое включение/выключение задач опроса энергетического объекта"""
    try:
        await ensure_object_permission(
            db,
            energetic_object_id=object_id,
            user=current_user,
            required_access=AccessLevel.READ_WRITE,
        )

        result = await db.execute(
            select(DevicePollingTask).where(
                DevicePollingTask.energetic_object_id == object_id
            ).order_by(DevicePollingTask.created_at.desc())
        )
        tasks = list(result.scalars().all())

        updated_tasks = await _set_tasks_active(db, tasks, payload.is_active)

        if updated_tasks:
            logger.info(
                f"Set {updated_tasks}/{len(tasks)} polling tasks for object {object_id} "
                f"to {'active' if payload.is_active else 'inactive'}"
            )
            await signal_worker_reload()

        return _build_bulk_update_response(
            scope="object",
            scope_id=object_id,
            energetic_object_ids=[object_id],
            is_active=payload.is_active,
            tasks=tasks,
            updated_tasks=updated_tasks,
        )

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error setting object polling tasks active: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to set object polling tasks active: {str(e)}"
        )


@router.patch(
    "/device/{device_id}/set-active",
    response_model=DevicePollingTasksBulkUpdateResponse,
    summary="Включить/выключить все задачи устройства",
    description="""
    Устанавливает статус активности для всех задач фонового опроса энергетических объектов,
    связанных с указанным энергоустройством через cor_bridges.
    """
)
async def set_device_polling_tasks_active(
    device_id: str,
    payload: DevicePollingTasksActivationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    """Массовое включение/выключение задач опроса по энергоустройству"""
    try:
        device_result = await db.execute(
            select(EnergeticDevice).where(EnergeticDevice.id == device_id)
        )
        device = device_result.scalar_one_or_none()
        if not device:
            device_result = await db.execute(
                select(EnergeticDevice).where(EnergeticDevice.device_id == device_id)
            )
            device = device_result.scalar_one_or_none()

        if not device:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Energetic device {device_id} not found"
            )

        if not is_admin_or_superadmin(current_user) and device.owner_cor_id != current_user.cor_id:
            access = await get_device_access(
                db,
                device_id=device.id,
                accessing_user_cor_id=current_user.cor_id,
            )
            if not access or not has_required_access(access.access_level, AccessLevel.READ_WRITE):
                raise HTTPException(status_code=403, detail="Недостаточно прав для этого энергоустройства")

        objects_result = await db.execute(
            select(EnergeticObject).where(
                or_(
                    EnergeticObject.cor_bridges.contains([device.id]),
                    EnergeticObject.cor_bridges.contains([device.device_id]),
                )
            )
        )
        energetic_objects = list(objects_result.scalars().all())
        energetic_object_ids = [obj.id for obj in energetic_objects]

        for energetic_object_id in energetic_object_ids:
            await ensure_object_permission(
                db,
                energetic_object_id=energetic_object_id,
                user=current_user,
                required_access=AccessLevel.READ_WRITE,
            )

        if energetic_object_ids:
            tasks_result = await db.execute(
                select(DevicePollingTask).where(
                    DevicePollingTask.energetic_object_id.in_(energetic_object_ids)
                ).order_by(DevicePollingTask.created_at.desc())
            )
            tasks = list(tasks_result.scalars().all())
        else:
            tasks = []

        updated_tasks = await _set_tasks_active(db, tasks, payload.is_active)

        if updated_tasks:
            logger.info(
                f"Set {updated_tasks}/{len(tasks)} polling tasks for device {device.id} "
                f"to {'active' if payload.is_active else 'inactive'}"
            )
            await signal_worker_reload()

        return _build_bulk_update_response(
            scope="device",
            scope_id=device.id,
            energetic_object_ids=energetic_object_ids,
            is_active=payload.is_active,
            tasks=tasks,
            updated_tasks=updated_tasks,
        )

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error setting device polling tasks active: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to set device polling tasks active: {str(e)}"
        )


@router.get(
    "/{task_id}",
    response_model=DevicePollingTaskResponse,
    summary="Получить задачу по ID",
    description="Возвращает детали конкретной задачи опроса"
)
async def get_polling_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    """Получение задачи по ID"""
    try:
        result = await db.execute(
            select(DevicePollingTask).where(DevicePollingTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Polling task {task_id} not found"
            )
        await ensure_object_permission(
            db,
            energetic_object_id=task.energetic_object_id,
            user=current_user,
            required_access=AccessLevel.READ,
        )
        
        return DevicePollingTaskResponse.model_validate(task)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting polling task: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get polling task: {str(e)}"
        )


@router.patch(
    "/{task_id}",
    response_model=DevicePollingTaskResponse,
    summary="Обновить задачу опроса",
    description="Обновляет параметры задачи опроса (интервал, конфигурацию, статус активности)"
)
async def update_polling_task(
    task_id: str,
    task_update: DevicePollingTaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    """Обновление задачи опроса"""
    try:
        result = await db.execute(
            select(DevicePollingTask).where(DevicePollingTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Polling task {task_id} not found"
            )
        await ensure_object_permission(
            db,
            energetic_object_id=task.energetic_object_id,
            user=current_user,
            required_access=AccessLevel.READ_WRITE,
        )
        
        # Обновляем поля
        update_data = task_update.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if field == "task_type" and value is not None:
                setattr(task, field, value.value)
            else:
                setattr(task, field, value)
        
        await db.commit()
        await db.refresh(task)
        
        logger.info(f"Updated polling task {task_id}")
        await signal_worker_reload()
        return DevicePollingTaskResponse.model_validate(task)
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error updating polling task: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update polling task: {str(e)}"
        )


@router.patch(
    "/{task_id}/toggle",
    response_model=DevicePollingTaskResponse,
    summary="Включить/выключить задачу обьекта",
    description="Переключает статус активности задачи (вкл/выкл фоновый опрос)"
)
async def toggle_polling_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    """Переключение активности задачи"""
    try:
        result = await db.execute(
            select(DevicePollingTask).where(DevicePollingTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Polling task {task_id} not found"
            )
        await ensure_object_permission(
            db,
            energetic_object_id=task.energetic_object_id,
            user=current_user,
            required_access=AccessLevel.READ_WRITE,
        )
        
        task.is_active = not task.is_active
        
        await db.commit()
        await db.refresh(task)
        
        logger.info(f"Toggled polling task {task_id} to {'active' if task.is_active else 'inactive'}")
        await signal_worker_reload()
        return DevicePollingTaskResponse.model_validate(task)
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error toggling polling task: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to toggle polling task: {str(e)}"
        )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить задачу опроса",
    description="Удаляет задачу фонового опроса (фоновая задача также будет остановлена)"
)
async def delete_polling_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    """Удаление задачи опроса"""
    try:
        result = await db.execute(
            select(DevicePollingTask).where(DevicePollingTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Polling task {task_id} not found"
            )
        await ensure_object_permission(
            db,
            energetic_object_id=task.energetic_object_id,
            user=current_user,
            required_access=AccessLevel.READ_WRITE,
        )
        
        await db.delete(task)
        await db.commit()
        
        logger.info(f"Deleted polling task {task_id}")
        await signal_worker_reload()
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error deleting polling task: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete polling task: {str(e)}"
        )


@router.get(
    "/",
    response_model=List[DevicePollingTaskResponse],
    summary="Получить все задачи опроса энергообьекта",
    description="Возвращает список всех задач опроса во всей системе"
)
async def get_all_polling_tasks(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(admin_access)
):
    """Получение всех задач опроса"""
    try:
        query = select(DevicePollingTask)
        
        if active_only:
            query = query.where(DevicePollingTask.is_active == True)
        
        query = query.order_by(DevicePollingTask.created_at.desc())
        
        result = await db.execute(query)
        tasks = result.scalars().all()
        
        return [DevicePollingTaskResponse.model_validate(task) for task in tasks]
        
    except Exception as e:
        logger.error(f"Error getting all polling tasks: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get polling tasks: {str(e)}"
        )


@router.post(
    "/reload",
    status_code=status.HTTP_200_OK,
    summary="Перезагрузить задачи воркера",
    description="""
    Отправляет сигнал воркеру для немедленной перезагрузки задач из БД.
    Полезно после создания/обновления/удаления задач для применения изменений без ожидания.
    
    **Примечание:** Воркер также автоматически перезагружает задачи каждые 5 секунд.
    """
)
async def trigger_worker_reload(
    admin: User = Depends(admin_access)
):
    """
    Триггер немедленной перезагрузки задач воркера
    
    Отправляет внутренний HTTP запрос к воркеру (если доступен webhook endpoint)
    или просто возвращает success, полагаясь на автоматическую синхронизацию
    """
    try:
        await signal_worker_reload()
        logger.info("Worker reload triggered via API")
        
        return {
            "status": "success",
            "message": "Reload signal sent to worker via Redis"
        }
        
    except Exception as e:
        logger.error(f"Error triggering worker reload: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger worker reload: {str(e)}"
        )
