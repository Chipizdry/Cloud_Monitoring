from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.db import get_db
from backend.repository.energy.demo_solar_battery_v2 import (
    DEMO_V2_DEFAULT_RANGE_END_TIME,
    DEMO_V2_DEFAULT_RANGE_START_TIME,
    DEMO_V2_MAX_DAYS_BACK,
    DEMO_V2_MIN_DAYS_BACK,
    DemoSolarBatteryV2ValidationError,
    create_demo_solar_battery_v2_object,
    delete_demo_solar_battery_v2_global_algorithm,
    delete_demo_solar_battery_v2_object,
    delete_demo_solar_battery_v2_object_algorithm,
    get_demo_solar_battery_v2_global_algorithm,
    get_demo_solar_battery_v2_object,
    get_demo_solar_battery_v2_objects,
    simulate_demo_solar_battery_v2,
    update_demo_solar_battery_v2_global_algorithm,
    update_demo_solar_battery_v2_object,
    update_demo_solar_battery_v2_object_algorithm,
    upsert_demo_solar_battery_v2_global_algorithm,
    upsert_demo_solar_battery_v2_object_algorithm,
)
from backend.schemas.energy_demo_v2 import (
    DemoSolarBatteryV2AlgorithmCreate,
    DemoSolarBatteryV2AlgorithmResponse,
    DemoSolarBatteryV2AlgorithmUpdate,
    DemoSolarBatteryV2GlobalAlgorithmCreate,
    DemoSolarBatteryV2GlobalAlgorithmResponse,
    DemoSolarBatteryV2GlobalAlgorithmUpdate,
    DemoSolarBatteryV2ObjectCreate,
    DemoSolarBatteryV2ObjectResponse,
    DemoSolarBatteryV2ObjectUpdate,
    DemoSolarBatteryV2SimulationResponse,
)


router = APIRouter(
    prefix="/demo-solar-battery-v2",
    tags=["Demo Solar Battery V2"],
)


def _bad_request(error: DemoSolarBatteryV2ValidationError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


@router.get(
    "/objects",
    response_model=list[DemoSolarBatteryV2ObjectResponse],
    summary="Demo АКБ-Солар v2: список объектов",
)
async def list_objects(
    db: AsyncSession = Depends(get_db),
) -> list[DemoSolarBatteryV2ObjectResponse]:
    return await get_demo_solar_battery_v2_objects(db)


@router.post(
    "/objects",
    response_model=DemoSolarBatteryV2ObjectResponse,
    summary="Demo АКБ-Солар v2: создать объект",
)
async def create_object(
    payload: DemoSolarBatteryV2ObjectCreate,
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryV2ObjectResponse:
    return await create_demo_solar_battery_v2_object(db, payload)


@router.get(
    "/objects/{object_id}",
    response_model=DemoSolarBatteryV2ObjectResponse,
    summary="Demo АКБ-Солар v2: получить объект",
)
async def get_object(
    object_id: str,
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryV2ObjectResponse:
    obj = await get_demo_solar_battery_v2_object(db, object_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Объект не найден")
    return obj


@router.patch(
    "/objects/{object_id}",
    response_model=DemoSolarBatteryV2ObjectResponse,
    summary="Demo АКБ-Солар v2: изменить объект",
)
async def update_object(
    object_id: str,
    payload: DemoSolarBatteryV2ObjectUpdate,
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryV2ObjectResponse:
    try:
        obj = await update_demo_solar_battery_v2_object(db, object_id, payload)
    except DemoSolarBatteryV2ValidationError as error:
        raise _bad_request(error)

    if obj is None:
        raise HTTPException(status_code=404, detail="Объект не найден")
    return obj


@router.delete(
    "/objects/{object_id}",
    summary="Demo АКБ-Солар v2: удалить объект",
)
async def delete_object(
    object_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    is_deleted = await delete_demo_solar_battery_v2_object(db, object_id)
    if not is_deleted:
        raise HTTPException(status_code=404, detail="Объект не найден")
    return {"status": "deleted"}


@router.put(
    "/objects/{object_id}/algorithm",
    response_model=DemoSolarBatteryV2AlgorithmResponse,
    summary="Demo АКБ-Солар v2: добавить или заменить алгоритм объекта",
)
async def upsert_object_algorithm(
    object_id: str,
    payload: DemoSolarBatteryV2AlgorithmCreate,
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryV2AlgorithmResponse:
    try:
        algorithm = await upsert_demo_solar_battery_v2_object_algorithm(
            db,
            object_id,
            payload,
        )
    except DemoSolarBatteryV2ValidationError as error:
        raise _bad_request(error)

    if algorithm is None:
        raise HTTPException(status_code=404, detail="Объект не найден")
    return algorithm


@router.post(
    "/objects/{object_id}/algorithm",
    response_model=DemoSolarBatteryV2AlgorithmResponse,
    summary="Demo АКБ-Солар v2: добавить алгоритм объекта",
)
async def create_object_algorithm(
    object_id: str,
    payload: DemoSolarBatteryV2AlgorithmCreate,
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryV2AlgorithmResponse:
    return await upsert_object_algorithm(object_id, payload, db)


@router.get(
    "/objects/{object_id}/algorithm",
    response_model=DemoSolarBatteryV2AlgorithmResponse,
    summary="Demo АКБ-Солар v2: получить алгоритм объекта",
)
async def get_object_algorithm(
    object_id: str,
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryV2AlgorithmResponse:
    obj = await get_demo_solar_battery_v2_object(db, object_id)
    if obj is None or obj.correction_algorithm is None:
        raise HTTPException(status_code=404, detail="Объект или алгоритм не найден")
    return obj.correction_algorithm


@router.patch(
    "/objects/{object_id}/algorithm",
    response_model=DemoSolarBatteryV2AlgorithmResponse,
    summary="Demo АКБ-Солар v2: изменить алгоритм объекта",
)
async def update_object_algorithm(
    object_id: str,
    payload: DemoSolarBatteryV2AlgorithmUpdate,
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryV2AlgorithmResponse:
    try:
        algorithm = await update_demo_solar_battery_v2_object_algorithm(
            db,
            object_id,
            payload,
        )
    except DemoSolarBatteryV2ValidationError as error:
        raise _bad_request(error)

    if algorithm is None:
        raise HTTPException(status_code=404, detail="Объект или алгоритм не найден")
    return algorithm


@router.delete(
    "/objects/{object_id}/algorithm",
    summary="Demo АКБ-Солар v2: удалить алгоритм объекта",
)
async def delete_object_algorithm(
    object_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    is_deleted = await delete_demo_solar_battery_v2_object_algorithm(db, object_id)
    if not is_deleted:
        raise HTTPException(status_code=404, detail="Объект или алгоритм не найден")
    return {"status": "deleted"}


@router.get(
    "/global-algorithm",
    response_model=DemoSolarBatteryV2GlobalAlgorithmResponse | None,
    summary="Demo АКБ-Солар v2: получить глобальный алгоритм",
)
async def get_global_algorithm(
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryV2GlobalAlgorithmResponse | None:
    return await get_demo_solar_battery_v2_global_algorithm(db)


@router.put(
    "/global-algorithm",
    response_model=DemoSolarBatteryV2GlobalAlgorithmResponse,
    summary="Demo АКБ-Солар v2: добавить или заменить глобальный алгоритм",
)
async def upsert_global_algorithm(
    payload: DemoSolarBatteryV2GlobalAlgorithmCreate,
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryV2GlobalAlgorithmResponse:
    objects = await get_demo_solar_battery_v2_objects(db)
    default_support_power_kw = sum(obj.peak_power_kw for obj in objects)
    try:
        return await upsert_demo_solar_battery_v2_global_algorithm(
            db,
            payload,
            default_support_power_kw=default_support_power_kw,
        )
    except DemoSolarBatteryV2ValidationError as error:
        raise _bad_request(error)


# @router.patch(
#     "/global-algorithm",
#     response_model=DemoSolarBatteryV2GlobalAlgorithmResponse,
#     summary="Demo АКБ-Солар v2: изменить глобальный алгоритм",
# )
async def update_global_algorithm(
    payload: DemoSolarBatteryV2GlobalAlgorithmUpdate,
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryV2GlobalAlgorithmResponse:
    objects = await get_demo_solar_battery_v2_objects(db)
    default_support_power_kw = sum(obj.peak_power_kw for obj in objects)
    try:
        algorithm = await update_demo_solar_battery_v2_global_algorithm(
            db,
            payload,
            default_support_power_kw=default_support_power_kw,
        )
    except DemoSolarBatteryV2ValidationError as error:
        raise _bad_request(error)

    if algorithm is None:
        raise HTTPException(status_code=404, detail="Глобальный алгоритм не найден")
    return algorithm


@router.delete(
    "/global-algorithm",
    summary="Demo АКБ-Солар v2: удалить глобальный алгоритм",
)
async def delete_global_algorithm(
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    is_deleted = await delete_demo_solar_battery_v2_global_algorithm(db)
    if not is_deleted:
        raise HTTPException(status_code=404, detail="Глобальный алгоритм не найден")
    return {"status": "deleted"}


@router.get(
    "/simulate",
    response_model=DemoSolarBatteryV2SimulationResponse,
    summary="Demo АКБ-Солар v2: пересчитать графики",
)
async def simulate(
    days_back: int | None = Query(
        None,
        ge=DEMO_V2_MIN_DAYS_BACK,
        le=DEMO_V2_MAX_DAYS_BACK,
        description="Один день назад для всех объектов: от 1 до 14",
    ),
    start_time: str = Query(
        DEMO_V2_DEFAULT_RANGE_START_TIME,
        description="Начало временного диапазона данных, HH:MM",
    ),
    end_time: str = Query(
        DEMO_V2_DEFAULT_RANGE_END_TIME,
        description="Конец временного диапазона данных, HH:MM",
    ),
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryV2SimulationResponse:
    try:
        return await simulate_demo_solar_battery_v2(
            db,
            days_back=days_back,
            start_time=start_time,
            end_time=end_time,
        )
    except DemoSolarBatteryV2ValidationError as error:
        raise _bad_request(error)
