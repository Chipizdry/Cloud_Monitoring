from datetime import datetime
from math import ceil
from fastapi import APIRouter, Depends, Query
from typing import Optional
from backend.database.db import get_db
from backend.database.models import AccessLevel, User
from backend.repository.energy.flywheel_data_collector import compute_energy_wh, get_flywheel_measurements
from backend.repository.energy.energetic_object_access import ensure_object_permission
from backend.schemas.flywheel_data import FlywheelMeasurementsResponse, PaginatedResponse
from sqlalchemy.ext.asyncio import AsyncSession
from backend.services.user.auth import auth_service




router = APIRouter(prefix="/flywheel-data", tags=["Flywheel Data"])


@router.get("/{energetic_object_id}", 
            response_model=PaginatedResponse[FlywheelMeasurementsResponse],
            summary="Получить данные маховика для объекта",
            description="Получить данные маховика для объекта с поддержкой пагинации и фильтрации по времени.")
async def get_flywheel_data(
    energetic_object_id: str,
    skip: int = Query(1, ge=1, description="Номер страницы для пагинации (начинается с 1)"),
    limit: int = Query(100, ge=1, le=1000, description="Количество записей на страницу для пагинации"),
    start_time: Optional[datetime] = Query(None, description="Начальное время для фильтрации данных (ISO 8601 формат)"),
    end_time: Optional[datetime] = Query(None, description="Конечное время для фильтрации данных (ISO 8601 формат)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
):
    await ensure_object_permission(
        db,
        energetic_object_id=energetic_object_id,
        user=current_user,
        required_access=AccessLevel.READ,
    )
    measurements, total_count = await get_flywheel_measurements(db, energetic_object_id, skip, limit, start_time, end_time)
    total_pages = ceil(total_count / limit) if total_count > 0 else 0

    items = []
    for measurement in measurements:
        item = FlywheelMeasurementsResponse.model_validate(measurement).model_copy(
            update={
                "kinetic_energy_wh": compute_energy_wh(
                    measurement.flywheel_speed_rpm
                )
            }
        )
        items.append(item)

    return PaginatedResponse(
        items=items,
        total_count=total_count,
        page=skip,
        page_size=limit,
        total_pages=total_pages
    )
