from fastapi import APIRouter, HTTPException, Depends, status

from backend.database.db import get_db
from backend.services.user.auth import auth_service
from backend.database.models import User
from backend.services.shared.access import user_access
from backend.schemas import ResponseCorIdModel
from backend.repository.user import cor_id as repository_cor_id
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/medical/cor_id", tags=["Cor-Id"])


@router.post(
    "/show_corid_info",
    # response_model=ResponseCorIdModel,
    dependencies=[Depends(user_access)],
)
async def read_cor_id(
    cor_id: ResponseCorIdModel,
    user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    **Расшифровка COR-id** \n

    """
    if cor_id:
        cor_id = repository_cor_id.decode_corid(cor_id.cor_id)
    if cor_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="COR-Id not found or invalid"
        )
    return cor_id
