from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import EnergeticObject, EnergeticObjectAccess, User
from backend.repository.energy.cor_bridges import normalize_cor_bridge_ids
from backend.repository.energy.energetic_object_access import is_admin_or_superadmin
from backend.schemas.energetic_object import EnergeticObjectResponse


async def resolve_owner_cor_id_for_response(
    db: AsyncSession,
    obj: EnergeticObject,
    current_user: User,
) -> str | None:
    if obj.owner_cor_id:
        return obj.owner_cor_id
    if not current_user.cor_id:
        return None

    result = await db.execute(
        select(EnergeticObjectAccess.granting_user_cor_id)
        .where(
            EnergeticObjectAccess.energetic_object_id == obj.id,
            EnergeticObjectAccess.accessing_user_cor_id == current_user.cor_id,
            EnergeticObjectAccess.granting_user_cor_id.is_not(None),
        )
        .order_by(EnergeticObjectAccess.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def build_object_response(
    db: AsyncSession,
    obj: EnergeticObject,
    current_user: User,
) -> EnergeticObjectResponse:
    owner_cor_id = await resolve_owner_cor_id_for_response(db, obj, current_user)
    can_view_secret = is_admin_or_superadmin(current_user) or (
        owner_cor_id is not None and owner_cor_id == current_user.cor_id
    )

    response = EnergeticObjectResponse.model_validate(obj)
    return response.model_copy(
        update={
            "owner_cor_id": owner_cor_id,
            "cor_bridges": await normalize_cor_bridge_ids(db, response.cor_bridges),
            "telegram_bot_token": response.telegram_bot_token if can_view_secret else None,
        }
    )
