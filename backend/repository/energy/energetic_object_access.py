from __future__ import annotations

from typing import List, Optional, Union

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.config import settings
from backend.database.models import (
    AccessLevel,
    EnergeticDevice,
    EnergeticDeviceAccess,
    EnergeticObject,
    EnergeticObjectAccess,
    User,
)


_ACCESS_PRIORITY = {
    AccessLevel.READ: 1,
    AccessLevel.READ_WRITE: 2,
    AccessLevel.SHARE: 3,
}


def is_admin_or_superadmin(user: User) -> bool:
    if user.email in settings.superadmin_emails:
        return True
    return user.has_role("admin")


def _normalize_access_level(access_level: Union[AccessLevel, str]) -> AccessLevel:
    value_to_enum = {
        "read": AccessLevel.READ,
        "read_write": AccessLevel.READ_WRITE,
        "share": AccessLevel.SHARE,
    }
    if isinstance(access_level, AccessLevel):
        return access_level
    lowercase_value = access_level.lower()
    if lowercase_value not in value_to_enum:
        raise HTTPException(
            status_code=400,
            detail="Неверный уровень доступа. Используйте один из: read, read_write, share",
        )
    return value_to_enum[lowercase_value]


def has_required_access(actual_access: AccessLevel, required_access: AccessLevel) -> bool:
    if actual_access is None:
        return False
    return _ACCESS_PRIORITY[actual_access] >= _ACCESS_PRIORITY[required_access]


async def get_object_by_id(db: AsyncSession, object_id: str) -> Optional[EnergeticObject]:
    result = await db.execute(select(EnergeticObject).where(EnergeticObject.id == object_id))
    return result.scalar_one_or_none()


async def get_object_access(
    db: AsyncSession, *, energetic_object_id: str, accessing_user_cor_id: str
) -> Optional[EnergeticObjectAccess]:
    result = await db.execute(
        select(EnergeticObjectAccess).where(
            EnergeticObjectAccess.energetic_object_id == energetic_object_id,
            EnergeticObjectAccess.accessing_user_cor_id == accessing_user_cor_id,
        )
    )
    return result.scalar_one_or_none()


async def list_objects_for_user(db: AsyncSession, user: User) -> List[EnergeticObject]:
    if is_admin_or_superadmin(user):
        result = await db.execute(select(EnergeticObject))
        return result.scalars().all()

    owned_result = await db.execute(select(EnergeticObject).where(EnergeticObject.owner_cor_id == user.cor_id))
    owned = {obj.id: obj for obj in owned_result.scalars().all()}

    shared_result = await db.execute(
        select(EnergeticObject)
        .join(EnergeticObjectAccess, EnergeticObject.id == EnergeticObjectAccess.energetic_object_id)
        .where(EnergeticObjectAccess.accessing_user_cor_id == user.cor_id)
    )
    for obj in shared_result.scalars().all():
        owned.setdefault(obj.id, obj)

    return list(owned.values())


async def ensure_object_permission(
    db: AsyncSession,
    *,
    energetic_object_id: str,
    user: User,
    required_access: AccessLevel,
) -> EnergeticObject:
    energetic_object = await get_object_by_id(db, energetic_object_id)
    if not energetic_object:
        raise HTTPException(status_code=404, detail="Энергетический объект не найден")

    if is_admin_or_superadmin(user):
        return energetic_object

    if energetic_object.owner_cor_id == user.cor_id:
        return energetic_object

    access = await get_object_access(
        db,
        energetic_object_id=energetic_object_id,
        accessing_user_cor_id=user.cor_id,
    )
    if not access or not has_required_access(access.access_level, required_access):
        raise HTTPException(status_code=403, detail="Недостаточно прав для этого энергетического объекта")

    return energetic_object


async def upsert_object_access(
    db: AsyncSession,
    *,
    energetic_object_id: str,
    accessing_user_cor_id: str,
    access_level: Union[AccessLevel, str],
    granting_user_cor_id: Optional[str] = None,
) -> EnergeticObjectAccess:
    normalized_access_level = _normalize_access_level(access_level)

    energetic_object = await get_object_by_id(db, energetic_object_id)
    if not energetic_object:
        raise HTTPException(status_code=404, detail="Энергетический объект не найден")

    access = await get_object_access(
        db,
        energetic_object_id=energetic_object_id,
        accessing_user_cor_id=accessing_user_cor_id,
    )

    if access:
        access.access_level = normalized_access_level
        access.granting_user_cor_id = granting_user_cor_id or access.granting_user_cor_id
    else:
        access = EnergeticObjectAccess(
            energetic_object_id=energetic_object_id,
            accessing_user_cor_id=accessing_user_cor_id,
            access_level=normalized_access_level,
            granting_user_cor_id=granting_user_cor_id,
        )
        db.add(access)

    cor_bridges = energetic_object.cor_bridges or []
    if cor_bridges:
        # cor_bridges may contain EnergeticDevice.id (UUID) or EnergeticDevice.device_id (e.g. COR-...)
        bridge_result = await db.execute(
            select(EnergeticDevice).where(
                EnergeticDevice.id.in_(cor_bridges) | EnergeticDevice.device_id.in_(cor_bridges)
            )
        )
        bridge_devices = bridge_result.scalars().all()

        for bridge_device in bridge_devices:
            bridge_access_result = await db.execute(
                select(EnergeticDeviceAccess).where(
                    EnergeticDeviceAccess.device_id == bridge_device.id,
                    EnergeticDeviceAccess.accessing_user_cor_id == accessing_user_cor_id,
                )
            )
            bridge_access = bridge_access_result.scalar_one_or_none()

            if bridge_access:
                bridge_access.access_level = AccessLevel.READ
                bridge_access.granting_user_cor_id = (
                    granting_user_cor_id or bridge_access.granting_user_cor_id
                )
            else:
                db.add(
                    EnergeticDeviceAccess(
                        device_id=bridge_device.id,
                        accessing_user_cor_id=accessing_user_cor_id,
                        access_level=AccessLevel.READ,
                        granting_user_cor_id=granting_user_cor_id,
                    )
                )

        found_refs = {d.id for d in bridge_devices} | {d.device_id for d in bridge_devices}
        missing_bridge_ids = sorted(set(cor_bridges) - found_refs)
        if missing_bridge_ids:
            logger.warning(
                "Some cor_bridges were not found and were skipped during share for "
                f"object {energetic_object_id}: {missing_bridge_ids}"
            )

    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        message = str(getattr(exc, "orig", exc))
        logger.error(f"SQLAlchemy error during energetic object access commit: {message}")
        if "enum" in message.lower() and "accesslevel" in message.lower():
            raise HTTPException(
                status_code=400,
                detail="Неверное значение access_level. Допустимые: read, read_write, share",
            )
        raise

    await db.refresh(access)
    return access


async def list_object_accesses(db: AsyncSession, energetic_object_id: str) -> List[EnergeticObjectAccess]:
    result = await db.execute(
        select(EnergeticObjectAccess).where(EnergeticObjectAccess.energetic_object_id == energetic_object_id)
    )
    return result.scalars().all()


async def revoke_object_access(
    db: AsyncSession,
    *,
    energetic_object_id: str,
    accessing_user_cor_id: str,
) -> None:
    energetic_object = await get_object_by_id(db, energetic_object_id)
    if not energetic_object:
        raise HTTPException(status_code=404, detail="Энергетический объект не найден")

    access = await get_object_access(
        db,
        energetic_object_id=energetic_object_id,
        accessing_user_cor_id=accessing_user_cor_id,
    )
    if not access:
        raise HTTPException(status_code=404, detail="Доступ не найден")

    cor_bridges = energetic_object.cor_bridges or []
    if cor_bridges:
        bridge_result = await db.execute(
            select(EnergeticDevice).where(
                EnergeticDevice.id.in_(cor_bridges) | EnergeticDevice.device_id.in_(cor_bridges)
            )
        )
        bridge_devices = bridge_result.scalars().all()

        for bridge_device in bridge_devices:
            bridge_access_result = await db.execute(
                select(EnergeticDeviceAccess).where(
                    EnergeticDeviceAccess.device_id == bridge_device.id,
                    EnergeticDeviceAccess.accessing_user_cor_id == accessing_user_cor_id,
                )
            )
            bridge_access = bridge_access_result.scalar_one_or_none()
            if not bridge_access:
                continue

            # Удаляем только связанный шаринг от того же grantor (или старый без grantor)
            if bridge_access.granting_user_cor_id in (None, access.granting_user_cor_id):
                await db.delete(bridge_access)

    await db.delete(access)
    await db.commit()
