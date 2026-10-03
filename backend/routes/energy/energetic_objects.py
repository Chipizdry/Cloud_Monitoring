from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select
from backend.database.db import get_db
from backend.database.redis_db import signal_broadcast_reload, signal_worker_reload
from backend.config.config import settings
from backend.database.models import AccessLevel, Profile, User
from backend.repository.energy.cerbo_service import (
    create_energetic_object,
    delete_energetic_object,
    update_energetic_object,
)
from backend.repository.energy.energetic_object_access import (
    ensure_object_permission,
    list_object_accesses,
    list_objects_for_user,
    revoke_object_access,
    upsert_object_access,
)
from backend.repository.energy.energetic_object_response import build_object_response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.energetic_object import (
    EnergeticObjectAccessCreate,
    EnergeticObjectAccessResponse,
    EnergeticObjectCreate,
    EnergeticObjectResponse,
    EnergeticObjectShareUserResponse,
    EnergeticObjectUpdate,
)
from backend.services.shared.access import user_access
from backend.services.energy.websocket_broadcast import enqueue_on_demand_command
from backend.services.energy.inverter_preset_loader import (
    OnDemandValueError,
    build_on_demand_payload,
    get_on_demand_command,
    get_preset,
    on_demand_command_uses_value,
)
from backend.services.user.cipher import decode_base64_with_padding, decrypt_data
from backend.services.user.auth import auth_service

router = APIRouter(prefix="/energetic_objects", tags=["Energetic Objects"])


class OnDemandCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_name: str = Field(
        ..., min_length=1, description="Имя команды из on_demand_commands пресета инвертора."
    )
    value: str | int | float | None = Field(
        None,
        description="Значение для команд с шаблоном {value}; для остальных команд игнорируется.",
    )

    @field_validator("value", mode="before")
    @classmethod
    def reject_boolean_value(cls, value):
        if isinstance(value, bool):
            raise ValueError("value must be a string or number")
        return value


async def _signal_task_reloads_for_object(obj: EnergeticObjectResponse) -> None:
    await signal_worker_reload()
    await signal_broadcast_reload()


@router.get(
    "/share/users",
    response_model=List[EnergeticObjectShareUserResponse],
)
async def list_users_for_object_sharing(
    search: Optional[str] = Query(None, description="Поиск по email или cor_id"),
    limit: int = Query(50, ge=1, le=200),
    object_id: Optional[str] = Query(None, description="ID энергообъекта для проверки права share"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    if object_id:
        await ensure_object_permission(
            db,
            energetic_object_id=object_id,
            user=current_user,
            required_access=AccessLevel.SHARE,
        )

    stmt = (
        select(User, Profile)
        .outerjoin(Profile, Profile.user_id == User.id)
        .where(User.is_active == True, User.cor_id.is_not(None), User.cor_id != current_user.cor_id)
        .order_by(User.email.asc())
        .limit(limit)
    )

    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                User.email.ilike(pattern),
                User.cor_id.ilike(pattern),
            )
        )

    rows = (await db.execute(stmt)).all()
    decoded_key: bytes | None = None
    try:
        decoded_key = decode_base64_with_padding(settings.aes_key)
    except ValueError:
        # Do not fail sharing UX because of malformed key format in env.
        decoded_key = None

    result: List[EnergeticObjectShareUserResponse] = []
    for user_obj, profile in rows:
        first_name = None
        surname = None

        if decoded_key and profile and profile.encrypted_first_name:
            try:
                first_name = await decrypt_data(profile.encrypted_first_name, decoded_key)
            except ValueError:
                first_name = None

        if decoded_key and profile and profile.encrypted_surname:
            try:
                surname = await decrypt_data(profile.encrypted_surname, decoded_key)
            except ValueError:
                surname = None

        result.append(
            EnergeticObjectShareUserResponse(
                email=user_obj.email,
                cor_id=user_obj.cor_id,
                first_name=first_name,
                surname=surname,
            )
        )

    return result


@router.post("/", response_model=EnergeticObjectResponse)
async def create_object(
    obj_data: EnergeticObjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    obj = await create_energetic_object(db, obj_data, owner_cor_id=current_user.cor_id)
    await _signal_task_reloads_for_object(obj)
    return await build_object_response(db, obj, current_user)

@router.get("/", response_model=list[EnergeticObjectResponse])
async def list_objects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    objects = await list_objects_for_user(db, current_user)
    return [
        await build_object_response(db, obj, current_user)
        for obj in objects
    ]

@router.get("/{object_id}", response_model=EnergeticObjectResponse)
async def read_object(
    object_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    obj = await ensure_object_permission(
        db,
        energetic_object_id=object_id,
        user=current_user,
        required_access=AccessLevel.READ,
    )
    return await build_object_response(db, obj, current_user)


@router.get("/{object_id}/commands")
async def list_on_demand_commands(
    object_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    obj = await ensure_object_permission(
        db, energetic_object_id=object_id, user=current_user, required_access=AccessLevel.READ
    )
    preset = get_preset(obj.vendor, obj.model_name) if obj.vendor and obj.model_name else None
    return {
        "object_id": object_id,
        "commands": [
            {"name": cmd.get("name"), "label": cmd.get("label"), "command_type": cmd.get("command_type"),
             "requires_value": on_demand_command_uses_value(cmd)}
            for cmd in (preset or {}).get("on_demand_commands", [])
        ],
    }


@router.post(
    "/{object_id}/commands",
    status_code=202,
    description="Ставит команду из on_demand_commands в приоритетную очередь каждого COR bridge объекта. Ответы инверторов доступны в существующем WebSocket потоке устройств.",
)
async def send_on_demand_command(
    object_id: str,
    request: OnDemandCommandRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    obj = await ensure_object_permission(
        db, energetic_object_id=object_id, user=current_user, required_access=AccessLevel.READ_WRITE
    )
    command = (
        get_on_demand_command(obj.vendor, obj.model_name, request.command_name)
        if obj.vendor and obj.model_name else None
    )
    if command is None:
        raise HTTPException(status_code=404, detail="Команда не найдена в пресете инвертора")

    bridges = list(obj.cor_bridges or [])
    if not bridges:
        raise HTTPException(status_code=400, detail="К объекту не привязан COR bridge")

    try:
        payload = build_on_demand_payload(command, request.value)
    except OnDemandValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"Некорректный пресет команды: {exc}") from exc

    bridge_requests = []
    for bridge_session_id in dict.fromkeys(bridges):
        request_id = await enqueue_on_demand_command(bridge_session_id, payload)
        bridge_requests.append({
            "bridge_session_id": bridge_session_id,
            "status": "queued" if request_id else "offline",
            "request_id": request_id,
        })

    queued_count = sum(item["status"] == "queued" for item in bridge_requests)
    if not queued_count:
        raise HTTPException(status_code=404, detail="Ни один COR bridge объекта не подключен")

    return {
        "status": "queued" if queued_count == len(bridge_requests) else "partial",
        "object_id": object_id,
        "command_name": request.command_name,
        "command_type": payload["command_type"],
        "queued_count": queued_count,
        "bridge_requests": bridge_requests,
    }

@router.put("/{object_id}", response_model=EnergeticObjectResponse)
async def update_object(
    object_id: str,
    obj_data: EnergeticObjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    await ensure_object_permission(
        db,
        energetic_object_id=object_id,
        user=current_user,
        required_access=AccessLevel.SHARE,
    )
    obj = await update_energetic_object(db, object_id, obj_data, owner_cor_id=current_user.cor_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")
    await _signal_task_reloads_for_object(obj)
    return await build_object_response(db, obj, current_user)

@router.delete("/{object_id}")
async def delete_object(
    object_id: str,
    cascade: bool = Query(False, description="Удалить объект вместе со всеми связанными данными (измерениями и расписаниями)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    await ensure_object_permission(
        db,
        energetic_object_id=object_id,
        user=current_user,
        required_access=AccessLevel.SHARE,
    )
    success = await delete_energetic_object(db, object_id, cascade=cascade)
    if not success:
        raise HTTPException(status_code=404, detail="Object not found")
    await signal_worker_reload()
    await signal_broadcast_reload()
    return {"status": "deleted", "cascade": cascade}


@router.post(
    "/share",
    response_model=EnergeticObjectAccessResponse,
)
async def share_energetic_object(
    payload: EnergeticObjectAccessCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    if payload.accessing_user_cor_id == current_user.cor_id:
        raise HTTPException(status_code=400, detail="Нельзя выдать доступ самому себе")

    await ensure_object_permission(
        db,
        energetic_object_id=payload.energetic_object_id,
        user=current_user,
        required_access=AccessLevel.SHARE,
    )

    return await upsert_object_access(
        db,
        energetic_object_id=payload.energetic_object_id,
        accessing_user_cor_id=payload.accessing_user_cor_id,
        access_level=payload.access_level,
        granting_user_cor_id=current_user.cor_id,
    )


@router.get(
    "/{object_id}/access",
    response_model=List[EnergeticObjectAccessResponse],
)
async def get_energetic_object_accesses(
    object_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    await ensure_object_permission(
        db,
        energetic_object_id=object_id,
        user=current_user,
        required_access=AccessLevel.SHARE,
    )
    return await list_object_accesses(db, object_id)


@router.delete("/{object_id}/access/{accessing_user_cor_id}")
async def revoke_energetic_object_sharing(
    object_id: str,
    accessing_user_cor_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    await ensure_object_permission(
        db,
        energetic_object_id=object_id,
        user=current_user,
        required_access=AccessLevel.SHARE,
    )
    await revoke_object_access(
        db,
        energetic_object_id=object_id,
        accessing_user_cor_id=accessing_user_cor_id,
    )
    return {"detail": "Доступ к энергетическому объекту отозван"}
