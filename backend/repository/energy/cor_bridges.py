from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import EnergeticDevice


async def normalize_cor_bridge_ids(db: AsyncSession, cor_bridges: list[str] | None) -> list[str]:
    if not cor_bridges:
        return []

    bridge_ids = [str(bridge_id).strip() for bridge_id in cor_bridges if bridge_id]
    if not bridge_ids:
        return []

    result = await db.execute(
        select(EnergeticDevice).where(
            or_(
                EnergeticDevice.id.in_(bridge_ids),
                EnergeticDevice.device_id.in_(bridge_ids),
            )
        )
    )
    devices = result.scalars().all()

    normalized_map: dict[str, str] = {}
    for device in devices:
        normalized_map[str(device.id)] = device.device_id
        normalized_map[device.device_id] = device.device_id

    normalized = [normalized_map.get(bridge_id, bridge_id) for bridge_id in bridge_ids]
    normalized = list(dict.fromkeys(normalized))

    if normalized != bridge_ids:
        logger.info(f"Normalized cor_bridges identifiers from {bridge_ids} to {normalized}")

    return normalized
