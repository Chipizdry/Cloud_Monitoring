"""
API для взаимодействия с энергетическими устройствами.
Напрямую использует логику из WebSocket сервера (работает в одном контейнере).
Только для энергетических устройств (Cerbo/Modbus).
"""
import asyncio
import re
from datetime import datetime
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, File, HTTPException, status, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from loguru import logger

from backend.database.db import get_db
from backend.database.models import EnergeticDevice, User
from backend.repository.energy import (
    get_device_access,
    get_device_by_id,
    get_assigned_device_ids,
    is_admin_or_superadmin,
    list_device_accesses,
    list_devices_for_user,
    list_all_devices,
    upsert_device_access,
    update_device,
    delete_device,
    delete_device_access,
    remove_own_device_access,
    upsert_energetic_device,
)

from backend.schemas.energetic_device import EnergeticDeviceAccessCreate, EnergeticDeviceAccessResponse, EnergeticDeviceCreate, EnergeticDeviceListResponse, EnergeticDeviceResponse, EnergeticDeviceUpdate, GetSettingsRequest, SendSettingsRequest, TracerouteRequest
from backend.services.shared.access import user_access, admin_access
from backend.services.energy.pi30_commands import PI30Command
from backend.services.energy.cor_bridge_broadcast_presets import (
    build_cor_bridge_preset_tasks,
    list_cor_bridge_broadcast_presets,
)
from backend.services.user.auth import auth_service
from backend.services.shared.websocket_events_manager import websocket_events_manager
from backend.database.redis_db import redis_client, signal_broadcast_reload, signal_worker_reload

# Импортируем логику из WebSocket сервера
from backend.routes.devices.websocket_routes import broadcast_manager

# тест пуш
class Pi30CommandItem(BaseModel):
    """Одна PI30 команда с опциональным значением"""
    pi30: str = Field(..., description="PI30 команда (например PGR<nn>, PCVV<nn.n>)")
    pi30_value: Optional[str] = Field(
        None,
        description=(
            "Опциональное значение для set-команд. "
            "Можно передать шаблон: PGR{value}, PGR<nn> или PGR{}; "
            "если шаблона нет, значение будет добавлено в конец команды."
        ),
    )



class Pi30ProxyRequest(BaseModel):
    """Запрос на отправку одной PI30 команды через proxy"""
    session_token: str
    pi30: str = Field(..., description="PI30 команда (например QPIGS, FWSTATUS, FWSET)")
    pi30_value: Optional[str] = Field(
        None,
        description=(
            "Опциональное значение для set-команд. "
            "Можно передать шаблон в pi30: PGR{value}, PGR<nn> или PGR{}; "
            "если шаблона нет, значение будет добавлено в конец команды (например PGR + 00 -> PGR00)."
        ),
    )
    pause_background_polling: bool = Field(
        False,
        description="Временно остановить активный фоновый polling для session_token до получения ответа",
    )
    response_timeout_seconds: float = Field(
        8.0,
        gt=0,
        le=120,
        description="Таймаут ожидания ответа устройства, после которого polling будет возобновлен",
    )


class Pi30BatchProxyRequest(BaseModel):
    """Запрос на отправку нескольких PI30 команд одновременно"""
    session_token: str
    commands: List[Pi30CommandItem] = Field(
        ...,
        min_items=1,
        description=(
            "Список команд для отправки. разделитель '\\n' "        ),
    )
    pause_background_polling: bool = Field(
        False,
        description="Временно остановить активный фоновый polling для session_token до получения ответа",
    )
    response_timeout_seconds: float = Field(
        3.0,
        gt=0,
        le=120,
        description="Таймаут ожидания ответа устройства после отправки всех команд, после которого polling будет возобновлен",
    )


router = APIRouter(prefix="/energetic", tags=["Energetic Devices"])


WEBSOCKET_REDIS_CLEANUP_PATTERNS = (
    "ws:session:*",
    "ws:connection:*",
    "ws:broadcast:task:*:running",
    "ws:broadcast:task:*:slot:*",
    "ws:broadcast:device:*:sending",
    "ws:status:settings-request:*",
)
WEBSOCKET_REDIS_CLEANUP_KEYS = ("ws:connections",)


def _build_pi30_command(command_template: str, command_value: Optional[str]) -> str:
    """Собирает PI30 команду с подстановкой значения для set-команд."""
    command = command_template.strip().upper()
    if not command:
        return command

    if command_value is None:
        return command

    value = command_value.strip().upper()
    if not value:
        raise HTTPException(status_code=400, detail="PI30 value must not be empty")

    placeholders = ("{VALUE}", "{value}", "{}", "<VALUE>", "<value>")
    for placeholder in placeholders:
        if placeholder in command:
            return command.replace(placeholder, value)

    if re.search(r"<[^>]+>", command):
        return re.sub(r"<[^>]+>", value, command)

    if command.startswith("Q"):
        raise HTTPException(
            status_code=400,
            detail="Value is not supported for query/read PI30 commands. Provide a set-command template or omit pi30_value.",
        )

    return f"{command}{value}"


async def _list_connected_devices_from_redis() -> List[Dict]:
    """Получает список WebSocket подключений устройств из Redis (глобально для всех воркеров)."""
    connection_ids = await redis_client.smembers("ws:connections")
    devices = []

    for connection_id in connection_ids:
        conn_data = await redis_client.hgetall(f"ws:connection:{connection_id}")
        if not conn_data:
            continue

        devices.append({
            "connection_id": connection_id,
            "session_id": conn_data.get("session_id"),
            "worker_id": conn_data.get("worker_id"),
            "connected_at": conn_data.get("connected_at"),
            "client_ip": conn_data.get("client_ip"),
        })

    return devices


# ============== Energetic device registry (DB) ==============


@router.get(
    "/devices",
    response_model=List[EnergeticDeviceListResponse],
    dependencies=[Depends(user_access)],
)
async def list_energetic_devices(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(auth_service.get_current_user)
):
    """Возвращает устройства, которыми владеет пользователь или к которым есть доступ."""
    assigned_device_ids = await get_assigned_device_ids(db)

    if is_admin_or_superadmin(current_user):
        devices = await list_all_devices(db)
    else:
        devices = await list_devices_for_user(db, current_user.cor_id)

    return [
        EnergeticDeviceListResponse.model_validate(device).model_copy(
            update={"is_assigned": device.id in assigned_device_ids}
        )
        for device in devices
    ]


@router.post(
    "/devices/create-manual",
    response_model=EnergeticDeviceResponse,
    dependencies=[Depends(user_access)],
)
async def create_energetic_device_manual(
    payload: EnergeticDeviceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
):
    """ Создать энергетическое устройство и привязать к текущему пользователю вручную."""
    device = await upsert_energetic_device(
        db,
        device_id=payload.device_id,
        owner_cor_id=current_user.cor_id,
        name=payload.name,
        model_name=payload.model_name,
        protocol=payload.protocol,
        description=payload.description,
    )
    return device


@router.get("/devices/connected", dependencies=[Depends(user_access)])
async def get_connected_energetic_devices():
    """
    Получает список подключенных энергетических устройств.
    Работает напрямую с WebSocket сервером в том же процессе.
    """
    devices = await _list_connected_devices_from_redis()
    return {"devices": devices, "count": len(devices)}


@router.get(
    "/devices/{device_id}",
    response_model=EnergeticDeviceResponse,
    dependencies=[Depends(user_access)],
)
async def get_energetic_device(
    device_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(auth_service.get_current_user)
):
    device = await get_device_by_id(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Энергетическое устройство не найдено")
    if not is_admin_or_superadmin(current_user) and device.owner_cor_id != current_user.cor_id:
        access = await get_device_access(
            db, device_id=device_id, accessing_user_cor_id=current_user.cor_id
        )
        if not access:
            raise HTTPException(status_code=403, detail="Нет доступа к устройству")
    return device


@router.patch(
    "/devices/{device_id}",
    response_model=EnergeticDeviceResponse,
    dependencies=[Depends(user_access)],
)
async def update_energetic_device(
    device_id: str,
    payload: EnergeticDeviceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
):
    """Обновить данные устройства (имя, описание, протокол, активность). Только владелец."""
    device = await get_device_by_id(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Энергетическое устройство не найдено")

    owner_for_update = current_user.cor_id
    if is_admin_or_superadmin(current_user):
        owner_for_update = device.owner_cor_id

    device = await update_device(
        db,
        device_id=device_id,
        owner_cor_id=owner_for_update,
        name=payload.name,
        model_name=payload.model_name,
        protocol=payload.protocol,
        description=payload.description,
        is_active=payload.is_active,
    )
    return device


@router.delete(
    "/devices/{device_id}/delete-manual",
    dependencies=[Depends(user_access)],
)
async def delete_energetic_device_manual(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
):
    """Удалить энергетическое устройство (только владелец)."""
    device = await get_device_by_id(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Энергетическое устройство не найдено")

    owner_for_delete = current_user.cor_id
    if is_admin_or_superadmin(current_user):
        owner_for_delete = device.owner_cor_id

    await delete_device(
        db,
        device_id=device_id,
        owner_cor_id=owner_for_delete,
    )
    return {"detail": "Устройство успешно удалено"}


@router.post(
    "/devices/share",
    response_model=EnergeticDeviceAccessResponse,
    dependencies=[Depends(user_access)],
)
async def share_energetic_device(
    payload: EnergeticDeviceAccessCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
):
    device_id = payload.device_id
    device = await get_device_by_id(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Энергетическое устройство не найдено")
    if not is_admin_or_superadmin(current_user) and device.owner_cor_id != current_user.cor_id:
        raise HTTPException(status_code=403, detail="Доступ для шаринга есть только у владельца")

    access = await upsert_device_access(
        db,
        device_id=device_id,
        accessing_user_cor_id=payload.accessing_user_cor_id,
        access_level=payload.access_level,
        granting_user_cor_id=current_user.cor_id,  # always set to current user
    )
    return access


@router.get(
    "/devices/{device_id}/access",
    response_model=List[EnergeticDeviceAccessResponse],
    dependencies=[Depends(user_access)],
)
async def list_energetic_device_accesses(
    device_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(auth_service.get_current_user)
):
    device = await get_device_by_id(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Энергетическое устройство не найдено")
    if not is_admin_or_superadmin(current_user) and device.owner_cor_id != current_user.cor_id:
        raise HTTPException(status_code=403, detail="Доступ к списку доступов есть только у владельца")

    return await list_device_accesses(db, device_id)


@router.delete(
    "/devices/{device_id}/access/users/{accessing_user_cor_id}",
    dependencies=[Depends(user_access)],
)
async def revoke_shared_energetic_device_access(
    device_id: str,
    accessing_user_cor_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
):
    """Отозвать доступ к устройству у конкретного пользователя (владелец или админ)."""
    device = await get_device_by_id(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Энергетическое устройство не найдено")

    owner_for_revoke = current_user.cor_id
    if is_admin_or_superadmin(current_user):
        owner_for_revoke = device.owner_cor_id

    await delete_device_access(
        db,
        device_id=device_id,
        accessing_user_cor_id=accessing_user_cor_id,
        owner_cor_id=owner_for_revoke,
    )
    return {"detail": "Доступ к устройству отозван"}


@router.delete(
    "/devices/{device_id}/access/me",
    dependencies=[Depends(user_access)],
)
async def decline_shared_energetic_device_access(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
):
    """Отказаться от расшаренного устройства (удаляет только личный доступ)."""
    device = await get_device_by_id(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Энергетическое устройство не найдено")

    if device.owner_cor_id == current_user.cor_id:
        raise HTTPException(
            status_code=400,
            detail="Владелец устройства не может отказаться от собственного устройства",
        )

    await remove_own_device_access(
        db,
        device_id=device_id,
        accessing_user_cor_id=current_user.cor_id,
    )
    return {"detail": "Доступ к устройству отозван для текущего пользователя"}


@router.get(
    "/admin/devices",
    response_model=List[EnergeticDeviceListResponse],
    dependencies=[Depends(admin_access)],
)
async def admin_list_all_energetic_devices(db: AsyncSession = Depends(get_db)):
    """Админский эндпоинт: возвращает все энергетические устройства со всей информацией о владельцах."""
    devices = await list_all_devices(db)
    assigned_device_ids = await get_assigned_device_ids(db)
    return [
        EnergeticDeviceListResponse.model_validate(device).model_copy(
            update={"is_assigned": device.id in assigned_device_ids}
        )
        for device in devices
    ]


@router.get("/devices/{session_id}/status", dependencies=[Depends(user_access)])
async def get_energetic_device_status(session_id: str):
    """
    Проверяет статус подключения энергетического устройства по session_id.
    Работает напрямую с WebSocket сервером в том же процессе.
    """
    connection_id = await redis_client.get(f"ws:session:{session_id}")
    if connection_id:
        conn_data = await redis_client.hgetall(f"ws:connection:{connection_id}")
        if conn_data:
            return {
                "session_id": session_id,
                "connected": True,
                "connection_id": connection_id,
                "connected_at": conn_data.get("connected_at"),
                "worker_id": conn_data.get("worker_id"),
                "client_ip": conn_data.get("client_ip"),
            }
    
    raise HTTPException(
        status_code=404, 
        detail=f"Energetic device with session_id {session_id} is not connected"
    )


@router.get("/health")
async def check_websocket_server_health():
    """
    Проверяет здоровье WebSocket сервера энергетических устройств.
    Работает напрямую в том же процессе.
    """
    connections_count = len(websocket_events_manager.active_connections)
    return {
        "status": "healthy",
        "service": "Energetic Devices WebSocket Server",
        "active_connections": connections_count
    }


@router.post("/send_message", dependencies=[Depends(user_access)])
async def send_message_to_energetic_device(session_token: str, data: Dict):
    """
    Отправляет сообщение на энергетическое устройство (Cerbo/Modbus).
    Устройство должно быть подключено через /ws/devices эндпоинт.
    
    Args:
        session_token: ID сессии устройства (тот же, что использовался при подключении WebSocket)
        data: Данные для отправки устройству (JSON объект)
    """
    try:
        await websocket_events_manager.send_to_session(
            session_id=session_token,
            event_data=data
        )
        return {"status": "success", "message": "Message sent to device"}
    except Exception as e:
        logger.error(f"Failed to send message to session {session_token}: {e}")
        raise HTTPException(
            status_code=404,
            detail=f"Энергетическое устройство с session_token {session_token} не подключено или произошла ошибка"
        )


@router.post("/pi30/send_command", dependencies=[Depends(user_access)])
async def send_pi30_command(request: Pi30ProxyRequest):
    """
    Отправляет одну PI30 команду на энергетическое устройство.
    
    Args:
        request: Запрос с session_token и pi30 командой
    """
    from backend.services.energy.pi30_commands import format_pi30_command_with_crc_hex
    
    command = _build_pi30_command(request.pi30, request.pi30_value)
    if not command:
        raise HTTPException(status_code=400, detail="PI30 command must not be empty")

    # Форматируем PI30 команду с CRC
    pi30_formatted = format_pi30_command_with_crc_hex(command)

    paused_task_ids: list[str] = []
    
    try:
        if request.pause_background_polling:
            tasks = await broadcast_manager.list_all()
            paused_task_ids = [
                str(task["id"])
                for task in tasks
                if task.get("session_id") == request.session_token and task.get("is_active")
            ]

            for task_id in paused_task_ids:
                await broadcast_manager.update_task(task_id, {"is_active": False}, run_local=False)

            if paused_task_ids:
                await signal_broadcast_reload()
                await signal_worker_reload()
                await asyncio.sleep(0.2)

        activity_version = broadcast_manager.session_activity_version.get(request.session_token, 0)
        bad_response_version = broadcast_manager.session_bad_response_version.get(request.session_token, 0)

        sent = await websocket_events_manager.send_to_session(
            session_id=request.session_token,
            event_data={"command_type": "pi30", "pi30": pi30_formatted}
        )
        if not sent:
            raise HTTPException(
                status_code=404,
                detail=f"Энергетическое устройство с session_token {request.session_token} не подключено",
            )

        response_received = None
        response_status = None
        if request.pause_background_polling:
            response_status = await broadcast_manager._wait_for_session_response(
                session_id=request.session_token,
                previous_activity_version=activity_version,
                previous_bad_response_version=bad_response_version,
                timeout_seconds=request.response_timeout_seconds,
            )
            response_received = response_status == "activity"
            if not response_received:
                await broadcast_manager._wait_for_session_cooldown(request.session_token)

        return {
            "status": "success",
            "message": f"PI30 command '{command}' sent to device",
            "resolved_command": command,
            "formatted_command": pi30_formatted,
            "pi30_value": request.pi30_value,
            "pause_background_polling": request.pause_background_polling,
            "response_received": response_received,
            "response_status": response_status,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send PI30 command to session {request.session_token}: {e}")
        raise HTTPException(
            status_code=404,
            detail=f"Энергетическое устройство с session_token {request.session_token} не подключено"
        )
    finally:
        if paused_task_ids:
            for task_id in paused_task_ids:
                try:
                    await broadcast_manager.update_task(task_id, {"is_active": True}, run_local=False)
                except Exception as restore_exc:
                    logger.error(f"Failed to restore broadcast task {task_id}: {restore_exc}")

            await signal_broadcast_reload()
            await signal_worker_reload()


@router.post("/pi30/send_batch_commands", dependencies=[Depends(user_access)])
async def send_batch_pi30_commands(request: Pi30BatchProxyRequest):
    """
    Отправляет несколько PI30 команд в одном склеенном формате с разделителем '\\n'.
    Команды обьединяются в один пейлоад и затем вычисляется общий CRC

    """
    from backend.services.energy.pi30_commands import format_pi30_command_with_crc_hex
    
    if not request.commands:
        raise HTTPException(status_code=400, detail="Commands list must not be empty")

    paused_task_ids: list[str] = []
    prepared_commands = []
    
    try:
        if request.pause_background_polling:
            tasks = await broadcast_manager.list_all()
            paused_task_ids = [
                str(task["id"])
                for task in tasks
                if task.get("session_id") == request.session_token and task.get("is_active")
            ]

            for task_id in paused_task_ids:
                await broadcast_manager.update_task(task_id, {"is_active": False}, run_local=False)

            if paused_task_ids:
                await signal_broadcast_reload()
                await signal_worker_reload()
                await asyncio.sleep(0.2)

        activity_version = broadcast_manager.session_activity_version.get(request.session_token, 0)
        bad_response_version = broadcast_manager.session_bad_response_version.get(request.session_token, 0)

        for idx, cmd_item in enumerate(request.commands):
            try:
                command = _build_pi30_command(cmd_item.pi30, cmd_item.pi30_value)
                if not command:
                    raise HTTPException(status_code=400, detail=f"Command at index {idx} is empty")

                prepared_commands.append({
                    "index": idx,
                    "resolved_command": command,
                    "pi30": cmd_item.pi30,
                    "pi30_value": cmd_item.pi30_value,
                })

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to send PI30 command #{idx}: {e}")

                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to build command at index {idx}: {str(e)}",
                )

        glued_payload = "\n".join(item["resolved_command"] for item in prepared_commands)
        glued_frame_hex = format_pi30_command_with_crc_hex(glued_payload)

        sent = await websocket_events_manager.send_to_session(
            session_id=request.session_token,
            event_data={"command_type": "pi30", "pi30": glued_frame_hex}
        )
        if not sent:
            raise HTTPException(
                status_code=404,
                detail=f"Энергетическое устройство с session_token {request.session_token} не подключено",
            )

        response_received = None
        response_status = None
        if request.pause_background_polling:
            response_status = await broadcast_manager._wait_for_session_response(
                session_id=request.session_token,
                previous_activity_version=activity_version,
                previous_bad_response_version=bad_response_version,
                timeout_seconds=request.response_timeout_seconds,
            )
            response_received = response_status == "activity"
            if not response_received:
                await broadcast_manager._wait_for_session_cooldown(request.session_token)

        return {
            "status": "success",
            "message": f"Batch of {len(prepared_commands)} commands sent as single glued frame",
            "commands_sent": len(prepared_commands),
            "glued_payload": glued_payload,
            "glued_frame_hex": glued_frame_hex,
            "results": prepared_commands,
            "pause_background_polling": request.pause_background_polling,
            "response_received": response_received,
            "response_status": response_status,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send batch PI30 commands to session {request.session_token}: {e}")
        raise HTTPException(
            status_code=404,
            detail=f"Ошибка при отправке пакета команд: {str(e)}"
        )
    finally:
        if paused_task_ids:
            for task_id in paused_task_ids:
                try:
                    await broadcast_manager.update_task(task_id, {"is_active": True}, run_local=False)
                except Exception as restore_exc:
                    logger.error(f"Failed to restore broadcast task {task_id}: {restore_exc}")

            await signal_broadcast_reload()
            await signal_worker_reload()


@router.get("/pi30/commands", dependencies=[Depends(user_access)])
async def list_pi30_commands():
    """
    Возвращает список доступных PI30 команд.
    """
    from backend.services.energy.pi30_commands import PI30_COMMAND_DESCRIPTIONS
    
    # Возвращаем список команд из описаний
    commands = [
        {
            "command": cmd.value,
            "description": PI30_COMMAND_DESCRIPTIONS.get(cmd, "")
        }
        for cmd in PI30Command
    ]
    return {"commands": commands}


# ===================== Firmware Update =====================

from pathlib import Path
from fastapi.responses import FileResponse

FIRMWARE_DIR = Path(__file__).resolve().parents[2] / "device_firmware"


class FirmwareUpdateRequest(BaseModel):
    """Запрос на обновление прошивки устройства"""
    session_token: str = Field(..., description="ID сессии устройства")
    filename: Optional[str] = Field(None, description="Имя файла прошивки из backend/device_firmware/. Если не указан, будет использован последний загруженный файл")


@router.get("/firmware/files", dependencies=[Depends(user_access)])
async def list_firmware_files():
    """
    Возвращает список файлов прошивок из папки backend/device_firmware/.
    Только для администраторов.
    """
    if not FIRMWARE_DIR.exists():
        return {"files": []}

    files = [
        {
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "modified_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
        }
        for p in sorted(FIRMWARE_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        if p.is_file()
    ]
    return {"files": files}





@router.get("/firmware/download/{filename}")
async def download_firmware(filename: str):
    """
    Скачивает файл прошивки для устройства.
    Используется устройством для скачивания обновления по HTTP ссылке.
    Без аутентификации - доступно устройством напрямую.
    
    Args:
        filename: Имя файла в папке backend/device_firmware/
    """
    try:
        firmware_path = (FIRMWARE_DIR / filename).resolve()
        
        # Проверяем что файл находится в папке FIRMWARE_DIR и существует
        if FIRMWARE_DIR not in firmware_path.parents:
            raise HTTPException(status_code=404, detail="Firmware file not found")
        
        if not firmware_path.is_file():
            raise HTTPException(status_code=404, detail="Firmware file not found")
        
        logger.info(f"Firmware download started: {filename}")
        return FileResponse(
            path=firmware_path,
            filename=firmware_path.name,
            media_type="application/octet-stream"
        )
    except Exception as e:
        logger.error(f"Failed to download firmware {filename}: {e}")
        raise HTTPException(status_code=404, detail="Firmware file not found")


@router.post("/firmware/update", dependencies=[Depends(user_access)])
async def update_device_firmware(request: FirmwareUpdateRequest):
    """
    Инициирует обновление прошивки на энергетическом устройстве.
    
    Процесс:
    1. Проверяет наличие файла в backend/device_firmware/{filename} или выбирает последний
    2. Генерирует публичную ссылку для скачивания
    3. Отправляет команду на устройство с URL прошивки
    4. Устройство скачивает и применяет обновление
    
    Args:
        request: session_token и опциональный filename
        
    Returns:
        {"status": "success", "message": "Firmware update initiated", "firmware_url": "..."}
    """
    try:
        # Определяем файл прошивки
        if request.filename:
            # Проверяем указанный файл
            firmware_path = (FIRMWARE_DIR / request.filename).resolve()
            
            if FIRMWARE_DIR not in firmware_path.parents or not firmware_path.is_file():
                raise HTTPException(
                    status_code=404,
                    detail=f"Firmware file '{request.filename}' not found in backend/device_firmware/"
                )
            
            selected_filename = request.filename
        else:
            # Автоматический выбор последнего файла
            if not FIRMWARE_DIR.exists():
                raise HTTPException(
                    status_code=404,
                    detail="Firmware directory does not exist"
                )
            
            files = [p for p in FIRMWARE_DIR.iterdir() if p.is_file()]
            if not files:
                raise HTTPException(
                    status_code=404,
                    detail="No firmware files found in backend/device_firmware/"
                )
            
            # Выбираем последний загруженный файл по времени модификации
            firmware_path = max(files, key=lambda p: p.stat().st_mtime)
            selected_filename = firmware_path.name
        
        base_url = "https://dev.monitoring.cor-int.com"  
        firmware_url = f"{base_url}/api/energetic/firmware/download/{selected_filename}"
        
        payload = {
            "command": "update_firmware",
            "firmware_url": firmware_url,
            "filename": selected_filename,
        }
        
        await websocket_events_manager.send_to_session(
            session_id=request.session_token,
            event_data=payload
        )
        
        logger.info(f"Firmware update initiated for {request.session_token}: {selected_filename}")
        
        return {
            "status": "success",
            "message": "Firmware update initiated",
            "session_token": request.session_token,
            "filename": selected_filename,
            "firmware_url": firmware_url
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to initiate firmware update for session {request.session_token}: {e}")
        raise HTTPException(
            status_code=404,
            detail=f"Энергетическое устройство с session_token {request.session_token} не подключено или произошла ошибка"
        )


# ===================== Broadcast Task Proxy =====================

from typing import Optional


class BroadcastTaskCreateProxy(BaseModel):
    """Запрос на создание фоновой рассылки команд"""
    task_name: str = Field(..., description="Название задачи")
    session_id: str = Field(..., description="ID устройства для отправки команд")
    command_type: str = Field(..., description="Тип команды: 'pi30', 'modbus_read' или 'modbus_tcp'")
    pi30_command: Optional[str] = Field(None, description="PI30 команда (например 'QPIGS') - только для command_type='pi30'")
    hex_data: Optional[str] = Field(None, description="Hex данные (например '09 03 00 00 00 10 45 4E') - только для command_type='modbus_read'")
    command_name: Optional[str] = Field(None, description="Имя команды для modbus_read (например 'grid')")
    command_payload: Optional[dict] = Field(
        None,
        description="Payload для command_type='modbus_tcp' (ip, port, unit_id, func, start_addr, quantity, value)",
    )
    interval_ms: float = Field(..., le=3600000, description="Интервал отправки команд в миллисекундах (поддерживает значения < 1000)")
    is_active: bool = Field(True, description="Запускать задачу сразу после создания")
    created_by: Optional[str] = Field(None, description="ID пользователя, создавшего задачу")


class BroadcastTaskPresetCreateProxy(BaseModel):
    """Запрос на создание набора фоновых рассылок по preset."""
    task_name_prefix: str = Field(..., description="Префикс названий задач")
    session_id: str = Field(..., description="ID устройства для отправки команд")
    preset_name: str = Field(..., description="Имя preset набора команд")
    slave_id: int = Field(1, ge=1, le=247, description="Modbus slave ID")
    interval_ms: float = Field(..., gt=0, le=3600000, description="Интервал отправки команд в миллисекундах")
    is_active: bool = Field(True, description="Запускать задачи сразу после создания")
    created_by: Optional[str] = Field(None, description="ID пользователя, создавшего задачи")


class BroadcastTaskUpdateProxy(BaseModel):
    """Запрос на обновление параметров существующей фоновой рассылки команд"""
    task_name: Optional[str] = Field(None, description="Новое название задачи")
    pi30_command: Optional[str] = Field(None, description="Новая PI30 команда (например 'QPIGS') - только для command_type='pi30'")
    hex_data: Optional[str] = Field(None, description="Новые hex данные (например '09 03 00 00 00 10 45 4E') - только для command_type='modbus_read'")
    command_name: Optional[str] = Field(None, description="Новое имя команды для modbus_read")
    command_payload: Optional[dict] = Field(
        None,
        description="Новый payload для command_type='modbus_tcp'",
    )
    interval_ms: Optional[float] = Field(None, le=3600000, description="Новый интервал отправки команд в миллисекундах (поддерживает значения < 1000)")
    is_active: Optional[bool] = Field(None, description="Включить/выключить задачу")


@router.get("/broadcast/tasks", dependencies=[Depends(user_access)])
async def list_broadcast_tasks():
    """
    Получить список всех фоновых рассылок команд.
    Возвращает задачи со статусом is_running и информацией о session_id.
    """
    tasks = await broadcast_manager.list_all()
    return {"tasks": tasks, "count": len(tasks)}


async def _resolve_broadcast_session_aliases(db: AsyncSession, session_id: str) -> list[str]:
    requested = str(session_id).strip()
    aliases = [requested] if requested else []
    if not requested:
        return aliases

    result = await db.execute(
        select(EnergeticDevice).where(
            or_(
                EnergeticDevice.id == requested,
                EnergeticDevice.device_id == requested,
            )
        )
    )
    device = result.scalar_one_or_none()
    if device:
        aliases.extend(alias for alias in (device.device_id, str(device.id)) if alias)

    return list(dict.fromkeys(aliases))


@router.get("/broadcast/presets", dependencies=[Depends(user_access)])
async def list_broadcast_presets():
    """Возвращает доступные preset-наборы фоновой рассылки для bridge устройств."""
    presets = list_cor_bridge_broadcast_presets()
    return {"presets": presets, "count": len(presets)}


@router.get("/broadcast/tasks/session/{session_id}", dependencies=[Depends(user_access)])
async def get_session_broadcast_tasks(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Получить все фоновые рассылки для конкретного устройства.
    Возвращает задачи, привязанные к session_id, с количеством активных задач.
    """
    session_aliases = await _resolve_broadcast_session_aliases(db, session_id)
    all_tasks = await broadcast_manager.list_all()
    session_tasks = [task for task in all_tasks if task.get("session_id") in session_aliases]
    
    if not session_tasks:
        raise HTTPException(
            status_code=404, 
            detail=f"Device {session_id} has no broadcast tasks"
        )
    
    active_count = sum(1 for task in session_tasks if task.get("is_active"))
    return {
        "session_id": session_id,
        "session_aliases": session_aliases,
        "tasks": session_tasks,
        "count": len(session_tasks),
        "active_count": active_count
    }


@router.post("/broadcast/tasks", dependencies=[Depends(user_access)], status_code=status.HTTP_201_CREATED)
async def create_broadcast_task(req: BroadcastTaskCreateProxy):
    """
    Создать новую фоновую рассылку команд на устройство.
    
    **Для PI30 команд:**
    - command_type="pi30", pi30_command="QPIGS" (команда автоматически форматируется с CRC)
    
    **Для Modbus команд:**
    - command_type="modbus_read", hex_data="09 03 00 00 00 10 45 4E"

    **Для Modbus TCP команд:**
    - command_type="modbus_tcp", command_payload={"ip":"91.203.25.12","port":502,"unit_id":100,"func":3,"start_addr":266,"quantity":1,"value":0}
    
    Задача сохраняется в БД и автоматически запускается если is_active=True.
    """
    from backend.schemas.websocket import WebSocketBroadcastTaskCreate
    
    task_data = WebSocketBroadcastTaskCreate(
        task_name=req.task_name,
        session_id=req.session_id,
        command_type=req.command_type,
        pi30_command=req.pi30_command,
        hex_data=req.hex_data,
        command_name=req.command_name,
        command_payload=req.command_payload,
        interval_ms=req.interval_ms,
        is_active=req.is_active,
        created_by=req.created_by
    )
    
    try:
        new_task = await broadcast_manager.create_and_start(task_data, run_local=False)
        await signal_broadcast_reload()
        return new_task
    except Exception as e:
        logger.error(f"Failed to create broadcast task: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create broadcast task: {str(e)}"
        )


@router.post("/broadcast/tasks/preset", dependencies=[Depends(user_access)], status_code=status.HTTP_201_CREATED)
async def create_broadcast_task_preset(req: BroadcastTaskPresetCreateProxy):
    """
    Создать набор фоновых рассылок из preset.

    Для COR bridge preset автоматически:
    - подставляется slave_id;
    - рассчитывается Modbus RTU CRC;
    - создаются отдельные broadcast задачи для каждого register group.
    """
    try:
        preset_tasks = build_cor_bridge_preset_tasks(
            preset_name=req.preset_name,
            task_name_prefix=req.task_name_prefix,
            session_id=req.session_id,
            slave_id=req.slave_id,
            interval_ms=req.interval_ms,
            is_active=req.is_active,
            created_by=req.created_by,
        )
        created_tasks = []
        for task_data in preset_tasks:
            created_tasks.append(await broadcast_manager.create_and_start(task_data, run_local=False))

        await signal_broadcast_reload()

        return {
            "preset_name": req.preset_name,
            "session_id": req.session_id,
            "slave_id": req.slave_id,
            "tasks": created_tasks,
            "count": len(created_tasks),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create preset broadcast tasks: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create preset broadcast tasks: {str(e)}"
        )


@router.post("/broadcast/tasks/reload", status_code=status.HTTP_200_OK)
async def reload_broadcast_tasks(
    admin: User = Depends(admin_access),
):
    """Принудительно запрашивает перезагрузку broadcast-задач в WebSocket-процессе."""
    try:
        await signal_broadcast_reload()
        logger.info(f"Broadcast tasks reload triggered by admin {admin.email}")
        return {
            "status": "success",
            "message": "Reload signal sent to broadcast worker via Redis",
        }
    except Exception as e:
        logger.error(f"Failed to trigger broadcast tasks reload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger broadcast tasks reload: {str(e)}"
        )


@router.post("/polling/tasks/reload", status_code=status.HTTP_200_OK)
async def reload_polling_tasks(
    admin: User = Depends(admin_access),
):
    """Принудительно запрашивает перезагрузку polling-задач в energy worker."""
    try:
        await signal_worker_reload()
        logger.info(f"Polling tasks reload triggered by admin {admin.email}")
        return {
            "status": "success",
            "message": "Reload signal sent to polling worker via Redis",
        }
    except Exception as e:
        logger.error(f"Failed to trigger polling tasks reload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger polling tasks reload: {str(e)}"
        )


@router.post("/debug/clear-websocket-redis", status_code=status.HTTP_200_OK)
async def clear_websocket_redis_state(
    admin: User = Depends(admin_access),
):
    """Очищает только runtime WebSocket-состояние в Redis."""
    try:
        deleted_by_pattern: Dict[str, int] = {}
        deleted_keys: Dict[str, int] = {}
        total_deleted = 0

        for pattern in WEBSOCKET_REDIS_CLEANUP_PATTERNS:
            batch = []
            pattern_deleted = 0

            async for key in redis_client.scan_iter(match=pattern, count=200):
                batch.append(key)
                if len(batch) >= 200:
                    deleted = await redis_client.delete(*batch)
                    pattern_deleted += int(deleted or 0)
                    batch = []

            if batch:
                deleted = await redis_client.delete(*batch)
                pattern_deleted += int(deleted or 0)

            deleted_by_pattern[pattern] = pattern_deleted
            total_deleted += pattern_deleted

        for key in WEBSOCKET_REDIS_CLEANUP_KEYS:
            deleted = int(await redis_client.delete(key) or 0)
            deleted_keys[key] = deleted
            total_deleted += deleted

        logger.warning(
            f"WebSocket Redis runtime state cleared by admin {admin.email}: "
            f"total_deleted={total_deleted}, patterns={deleted_by_pattern}, keys={deleted_keys}"
        )

        return {
            "status": "success",
            "deleted_total": total_deleted,
            "deleted_by_pattern": deleted_by_pattern,
            "deleted_keys": deleted_keys,
            "note": "Only volatile websocket runtime keys were removed; database records were not touched.",
        }
    except Exception as e:
        logger.error(f"Failed to clear websocket Redis runtime state: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear websocket Redis runtime state: {str(e)}",
        )


@router.patch("/broadcast/tasks/{task_id}", dependencies=[Depends(user_access)])
async def update_broadcast_task(task_id: str, req: BroadcastTaskUpdateProxy):
    """
    Обновить параметры существующей фоновой рассылки команд по устройствам.
    
    Позволяет изменить:
    - task_name: название задачи
    - pi30_command: команда (для command_type='pi30')
    - hex_data: hex данные (для command_type='modbus_read')
    - command_name: имя команды (для command_type='modbus_read')
    - command_payload: payload (для command_type='modbus_tcp')
    - interval_ms: интервал отправки команд в миллисекундах
    - is_active: статус активности (true/false)
    
    """
    from backend.schemas.websocket import WebSocketBroadcastTaskUpdate
    
    update_data = WebSocketBroadcastTaskUpdate(
        task_name=req.task_name,
        pi30_command=req.pi30_command,
        hex_data=req.hex_data,
        command_name=req.command_name,
        command_payload=req.command_payload,
        interval_ms=req.interval_ms,
        is_active=req.is_active
    )
    
    try:
        updated_task = await broadcast_manager.update_task(task_id, update_data, run_local=False)
        await signal_broadcast_reload()
        return updated_task
    except ValueError as e:
        logger.warning(f"Broadcast task '{task_id}' not found: {e}")
        raise HTTPException(status_code=404, detail=f"Broadcast task '{task_id}' not found")
    except Exception as e:
        logger.error(f"Failed to update broadcast task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update broadcast task: {str(e)}"
        )


@router.patch(
    "/broadcast/tasks/session/{session_id}/disable",
    dependencies=[Depends(user_access)],
    include_in_schema=False,
)
@router.patch("/broadcast/tasks/session/{session_id}/toggle", dependencies=[Depends(user_access)])
async def toggle_session_broadcast_tasks(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Включить/выключить все фоновые рассылки команд для конкретного устройства.
    Если есть активные задачи - выключает все. Если активных нет - включает все.
    """
    try:
        session_aliases = await _resolve_broadcast_session_aliases(db, session_id)
        last_error: ValueError | None = None
        used_session_id = session_id

        for session_alias in session_aliases:
            try:
                updated_tasks, is_active, updated_count = await broadcast_manager.toggle_tasks_for_session(
                    session_alias,
                    run_local=False,
                )
                used_session_id = session_alias
                break
            except ValueError as exc:
                last_error = exc
        else:
            raise last_error or ValueError(f"No broadcast tasks for session_id={session_id}")

        await signal_broadcast_reload()
        return {
            "status": "success",
            "session_id": session_id,
            "used_session_id": used_session_id,
            "session_aliases": session_aliases,
            "is_active": is_active,
            "total_tasks": len(updated_tasks),
            "updated_count": updated_count,
            "active_count": sum(1 for task in updated_tasks if task.is_active),
            "task_ids": [task.id for task in updated_tasks],
            "message": (
                f"Broadcast tasks {'enabled' if is_active else 'disabled'} "
                "and reload signal sent"
            ),
        }
    except ValueError as e:
        logger.warning(f"Broadcast tasks for session_id '{session_id}' not found: {e}")
        raise HTTPException(
            status_code=404,
            detail=f"Broadcast tasks for session_id '{session_id}' not found",
        )
    except Exception as e:
        logger.error(f"Failed to toggle broadcast tasks for session_id {session_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to toggle broadcast tasks for session_id: {str(e)}",
        )


@router.patch("/broadcast/tasks/{task_id}/toggle", dependencies=[Depends(user_access)])
async def toggle_broadcast_task(task_id: str):
    """
    Включить/выключить фоновую рассылку команды на устройстве.
    Переключает is_active и запускает/останавливает задачу без удаления из БД.
    """
    try:
        updated_task = await broadcast_manager.toggle_task(task_id, run_local=False)
        await signal_broadcast_reload()
        return updated_task
    except ValueError as e:
        logger.warning(f"Broadcast task '{task_id}' not found: {e}")
        raise HTTPException(status_code=404, detail=f"Broadcast task '{task_id}' not found")
    except Exception as e:
        logger.error(f"Failed to toggle broadcast task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to toggle broadcast task: {str(e)}"
        )


@router.delete("/broadcast/tasks/{task_id}", dependencies=[Depends(user_access)])
async def delete_broadcast_task(task_id: str):
    """
    Удалить фоновую рассылку команд.
    Останавливает задачу и удаляет из БД.
    """
    try:
        await broadcast_manager.stop_and_delete(task_id, run_local=False)
        await signal_broadcast_reload()
        return {
            "status": "success",
            "message": f"Broadcast task '{task_id}' deleted successfully"
        }
    except ValueError as e:
        logger.warning(f"Broadcast task '{task_id}' not found: {e}")
        raise HTTPException(status_code=404, detail=f"Broadcast task '{task_id}' not found")
    except Exception as e:
        logger.error(f"Failed to delete broadcast task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete broadcast task: {str(e)}"
        )


@router.post(
    "/send_settings",
    status_code=status.HTTP_200_OK,
    summary="Отправить настройки на энергетическое устройство"
)
async def send_settings_to_energetic_device(request: SendSettingsRequest):
    """
    Отправляет настройки на энергетическое устройство (Cerbo/Modbus) через WebSocket.
    
    Поддерживаемые поля настроек (все опциональны):
    - user: Настройки пользователя
    - account: Настройки аккаунта
    - network: Сетевые настройки
    - wifi: Настройки WiFi
    - uart: Настройки UART
    - system: Системные настройки
    - all: Все настройки сразу
    
    Только одно из полей должно быть заполнено. Устройство должно быть подключено.
    """
    try:
        # Собираем только заполненные поля
        settings_data = {}
        if request.user is not None:
            settings_data["user"] = request.user
        if request.account is not None:
            settings_data["account"] = request.account
        if request.network is not None:
            settings_data["network"] = request.network
        if request.wifi is not None:
            settings_data["wifi"] = request.wifi
        if request.uart is not None:
            settings_data["uart"] = request.uart
        if request.system is not None:
            settings_data["system"] = request.system
        if request.all is not None:
            settings_data["all"] = request.all
        
        # Проверяем, что хотя бы одно поле заполнено
        if not settings_data:
            raise HTTPException(
                status_code=400,
                detail="Необходимо заполнить хотя бы одно поле с настройками"
            )
        
        # Формируем сообщение с настройками
        message_data = {
            "command_type": "set_settings",
            "settings": settings_data
        }
        
        # Отправляем сообщение через websocket_events_manager
        sent = await websocket_events_manager.send_to_session(
            session_id=request.session_token,
            event_data=message_data,
        )
        if not sent:
            raise HTTPException(
                status_code=404,
                detail=f"Энергетическое устройство с session_token {request.session_token} не подключено",
            )
        
        logger.info(f"Settings sent to energetic device session {request.session_token}: {list(settings_data.keys())}")
        
        return {
            "detail": "Настройки успешно отправлены",
            "session_token": request.session_token,
            "settings_sent": list(settings_data.keys())
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при отправке настроек на устройство {request.session_token}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Ошибка при отправке настроек: {str(e)}"
        )


@router.post(
    "/get_settings",
    status_code=status.HTTP_200_OK,
    summary="Получить настройки с энергетического устройства"
)
async def get_settings_from_energetic_device(request: GetSettingsRequest):
    """
    Запрашивает настройки с энергетического устройства (Cerbo/Modbus) через WebSocket.
    
    Поддерживаемые флаги настроек (все bool):
    - user: Получить настройки пользователя
    - account: Получить настройки аккаунта
    - network: Получить сетевые настройки
    - wifi: Получить настройки WiFi
    - uart: Получить настройки UART
    - system: Получить системные настройки
    - all: Получить все настройки сразу
    
    Хотя бы один флаг должен быть True. Устройство должно быть подключено.
    """
    try:
        # Собираем запрошенные настройки
        settings_requested = []
        if request.user:
            settings_requested.append("user")
        if request.account:
            settings_requested.append("account")
        if request.network:
            settings_requested.append("network")
        if request.wifi:
            settings_requested.append("wifi")
        if request.uart:
            settings_requested.append("uart")
        if request.system:
            settings_requested.append("system")
        if request.all:
            settings_requested.append("all")
        
        # Проверяем, что хотя бы одно поле запрошено
        if not settings_requested:
            raise HTTPException(
                status_code=400,
                detail="Необходимо указать хотя бы один флаг настроек для получения"
            )
        
        # Формируем сообщение для запроса настроек
        message_data = {
            "command_type": "get_settings",
            "settings_requested": settings_requested
        }
        
        # Отправляем сообщение через websocket_events_manager
        sent = await websocket_events_manager.send_to_session(
            session_id=request.session_token,
            event_data=message_data,
        )
        if not sent:
            raise HTTPException(
                status_code=404,
                detail=f"Энергетическое устройство с session_token {request.session_token} не подключено",
            )
        
        logger.info(f"Settings request sent to energetic device session {request.session_token}: {settings_requested}")
        
        return {
            "detail": "Запрос настроек успешно отправлен",
            "session_token": request.session_token,
            "settings_requested": settings_requested
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при запросе настроек с устройства {request.session_token}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Ошибка при запросе настроек: {str(e)}"
        )


@router.post(
    "/traceroute",
    status_code=status.HTTP_200_OK,
    summary="Запустить трассировку маршрута на устройстве",
)
async def run_traceroute_on_device(request: TracerouteRequest):
    """Отправляет устройству команду traceroute через WebSocket."""
    try:
        message_data = {
            "command_type": "traceroute",
            "url": request.url,
        }

        sent = await websocket_events_manager.send_to_session(
            session_id=request.session_token,
            event_data=message_data,
        )
        if not sent:
            raise HTTPException(
                status_code=404,
                detail=f"Энергетическое устройство с session_token {request.session_token} не подключено",
            )
        logger.info(f"Traceroute command sent to energetic device session {request.session_token}: url={request.url}")

        return {
            "detail": "Команда traceroute успешно отправлена",
            "session_token": request.session_token,
            "url": request.url,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при запуске traceroute на устройстве {request.session_token}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при запуске traceroute: {str(e)}"
        )
