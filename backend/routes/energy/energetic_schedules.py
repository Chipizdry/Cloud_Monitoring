from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from backend.database.db import get_db
from backend.database.models import AccessLevel, User
from backend.repository.energy.cerbo_service import create_schedule, create_schedule_with_energetic_object_id, delete_schedule, get_all_schedules, get_all_schedules_by_object_id, get_schedule_by_id, update_schedule
from backend.repository.energy.energetic_object_access import ensure_object_permission
from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.energetic_schedule import EnergeticScheduleBase, EnergeticScheduleCreate, EnergeticScheduleCreateForObject, EnergeticScheduleResponse
from backend.services.shared.access import admin_access, user_access
from backend.services.user.auth import auth_service



router = APIRouter(prefix="/energetic_schedules", tags=["Energetic Schedules"])
# Добавить energetic object id - done

@router.post("/create", 
             response_model=EnergeticScheduleResponse, 
             status_code=status.HTTP_201_CREATED,
             tags=["Energetic Schedules"])
async def create_energetic_schedule(
    schedule_data: EnergeticScheduleCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(admin_access),
):
    new_schedule = await create_schedule(db, schedule_data)
    return new_schedule


@router.post("/v1/create", 
             response_model=EnergeticScheduleResponse, 
             status_code=status.HTTP_201_CREATED,
             tags=["Energetic Schedules"])
async def create_energetic_schedule(
    schedule_data: EnergeticScheduleCreateForObject,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    await ensure_object_permission(
        db,
        energetic_object_id=schedule_data.energetic_object_id,
        user=current_user,
        required_access=AccessLevel.READ_WRITE,
    )
    new_schedule = await create_schedule_with_energetic_object_id(db, schedule_data)
    return new_schedule



@router.get("/{schedule_id}", 
            response_model=EnergeticScheduleResponse,
            tags=["Energetic Schedules"])
async def get_energetic_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    schedule = await get_schedule_by_id(db, schedule_id)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    await ensure_object_permission(
        db,
        energetic_object_id=schedule.energetic_object_id,
        user=current_user,
        required_access=AccessLevel.READ,
    )
    return schedule

# Добавить energetic object id - done

@router.get("/", 
            response_model=List[EnergeticScheduleResponse],
            tags=["Energetic Schedules"])
async def get_all_energetic_schedules_api(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(admin_access),
):
    schedules = await get_all_schedules(db)
    response = []
    for schedule in schedules:
        schedule = EnergeticScheduleResponse(
            id=schedule.id,
            start_time=schedule.start_time,
            duration=schedule.duration,
            grid_feed_w=schedule.grid_feed_w,
            battery_level_percent=schedule.battery_level_percent,
            charge_battery_value=schedule.charge_battery_value,
            is_active=schedule.is_active,
            is_manual_mode=schedule.is_manual_mode,
            end_time=schedule.end_time
        )
        response.append(schedule)
    return response


@router.get("/v1/", 
            response_model=List[EnergeticScheduleResponse],
            tags=["Energetic Schedules"])
async def get_all_energetic_schedules_api(
    energetic_object_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    await ensure_object_permission(
        db,
        energetic_object_id=energetic_object_id,
        user=current_user,
        required_access=AccessLevel.READ,
    )
    schedules = await get_all_schedules_by_object_id(db=db, energetic_object_id=energetic_object_id)
    response = []
    for schedule in schedules:
        schedule = EnergeticScheduleResponse(
            id=schedule.id,
            start_time=schedule.start_time,
            duration=schedule.duration,
            grid_feed_w=schedule.grid_feed_w,
            battery_level_percent=schedule.battery_level_percent,
            charge_battery_value=schedule.charge_battery_value,
            is_active=schedule.is_active,
            is_manual_mode=schedule.is_manual_mode,
            end_time=schedule.end_time
        )
        response.append(schedule)
    return response



@router.put("/{schedule_id}", 
            response_model=EnergeticScheduleResponse,
            tags=["Energetic Schedules"])
async def update_energetic_schedule_api(
    schedule_id: str,
    schedule_data: EnergeticScheduleBase,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    existing_schedule = await get_schedule_by_id(db, schedule_id)
    if not existing_schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    await ensure_object_permission(
        db,
        energetic_object_id=existing_schedule.energetic_object_id,
        user=current_user,
        required_access=AccessLevel.READ_WRITE,
    )
    updated_schedule = await update_schedule(db, schedule_id, schedule_data)
    if not updated_schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return updated_schedule

@router.delete("/{schedule_id}", 
               status_code=status.HTTP_204_NO_CONTENT,
               tags=["Energetic Schedules"])
async def delete_energetic_schedule_api(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth_service.get_current_user),
    _: User = Depends(user_access),
):
    existing_schedule = await get_schedule_by_id(db, schedule_id)
    if not existing_schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    await ensure_object_permission(
        db,
        energetic_object_id=existing_schedule.energetic_object_id,
        user=current_user,
        required_access=AccessLevel.READ_WRITE,
    )
    deleted = await delete_schedule(db, schedule_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return


