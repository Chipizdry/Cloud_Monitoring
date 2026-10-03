from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.db import get_db
from backend.repository.energy.demo_solar_battery import (
    DEFAULT_RANGE_END_TIME,
    DEFAULT_RANGE_START_TIME,
    DEMO_CLOUD_EVENT,
    MEASUREMENTS_DEFAULT_BATTERY_CONFIG,
    MEASUREMENTS_SOURCE_OBJECT_NAME,
    compare_demo_solar_battery_measurements,
    get_demo_solar_battery_config,
)
from backend.schemas.energy_demo import (
    DemoCloudEvent,
    DemoInsolationProfile,
    DemoSolarBatteryComparisonResponse,
    DemoSolarBatteryConfigResponse,
)


router = APIRouter(
    prefix="/demo-solar-battery",
    tags=["Demo Solar Battery"]
    # dependencies=[Depends(user_access)],
)


@router.get(
    "/config",
    response_model=DemoSolarBatteryConfigResponse,
    summary="Demo АКБ-Солар: объекты и профиль инсоляции - УСТАРЕЛО",
)
async def get_demo_config(
    insolation_profile: DemoInsolationProfile = Query(
        DemoInsolationProfile.IDEAL,
        description="Профиль инсоляции: ideal - идеальный день, real - реальные W/m2",
    ),
) -> DemoSolarBatteryConfigResponse:
    return get_demo_solar_battery_config(insolation_profile=insolation_profile)


@router.get(
    "/compare",
    response_model=DemoSolarBatteryComparisonResponse,
    summary="Demo АКБ-Солар: сравнение реальной генерации и сглаживания АКБ - УСТАРЕЛО",
)
async def compare_demo_by_query(
    source_object_name: str = Query(
        MEASUREMENTS_SOURCE_OBJECT_NAME,
        description="Object name в cerbo_measurements",
    ),
    start_time: str = Query(
        DEFAULT_RANGE_START_TIME,
        description="Начало временного диапазона данных, HH:MM",
    ),
    end_time: str = Query(
        DEFAULT_RANGE_END_TIME,
        description="Конец временного диапазона данных, HH:MM",
    ),
    cloud: bool = Query(
        False,
        description="Временно игнорируется. Искусственная туча отключена.",
    ),
    cloud_start_time: str = Query(
        DEMO_CLOUD_EVENT.start_time,
        description="Временно игнорируется. Время появления искусственной тучи, HH:MM",
    ),
    cloud_duration_minutes: int = Query(
        DEMO_CLOUD_EVENT.duration_minutes,
        ge=1,
        description="Временно игнорируется. Длительность искусственной тучи в минутах",
    ),
    cloud_transmission_factor: float = Query(
        DEMO_CLOUD_EVENT.cloud_transmission_factor,
        ge=0,
        le=1,
        description="Временно игнорируется. Коэффициент пропускания тучи",
    ),
    battery_capacity_duration_minutes: float = Query(
        MEASUREMENTS_DEFAULT_BATTERY_CONFIG.capacity_duration_minutes,
        gt=0,
        description="Устаревший параметр. Оставлен для совместимости, в текущем алгоритме игнорируется.",
    ),
    initial_soc_percent: float = Query(
        MEASUREMENTS_DEFAULT_BATTERY_CONFIG.initial_soc_percent,
        ge=0,
        le=100,
        description="Устаревший параметр. Оставлен для совместимости, в текущем алгоритме игнорируется.",
    ),
    min_soc_percent: float = Query(
        MEASUREMENTS_DEFAULT_BATTERY_CONFIG.min_soc_percent,
        ge=0,
        le=100,
        description="Устаревший параметр. Оставлен для совместимости, в текущем алгоритме игнорируется.",
    ),
    max_discharge_power_kw: float | None = Query(
        MEASUREMENTS_DEFAULT_BATTERY_CONFIG.max_discharge_power_kw,
        ge=0,
        description="Устаревший параметр. Оставлен для совместимости, в текущем алгоритме игнорируется.",
    ),
    max_charge_power_kw: float | None = Query(
        MEASUREMENTS_DEFAULT_BATTERY_CONFIG.max_charge_power_kw,
        ge=0,
        description="Устаревший параметр. Оставлен для совместимости, в текущем алгоритме игнорируется.",
    ),
    spike_threshold_percent: float = Query(
        MEASUREMENTS_DEFAULT_BATTERY_CONFIG.spike_threshold_percent,
        ge=0,
        description="Устаревший параметр. Оставлен для совместимости, в текущем алгоритме игнорируется.",
    ),
    drop_compensation_duration_minutes: int = Query(
        MEASUREMENTS_DEFAULT_BATTERY_CONFIG.drop_compensation_duration_minutes,
        ge=1,
        description="Устаревший параметр. Оставлен для совместимости, в текущем алгоритме игнорируется.",
    ),
    db: AsyncSession = Depends(get_db),
) -> DemoSolarBatteryComparisonResponse:
    return await compare_demo_solar_battery_measurements(
        db,
        source_object_name=source_object_name,
        battery_config=MEASUREMENTS_DEFAULT_BATTERY_CONFIG,
        cloud_enabled=False,
        cloud_event=DemoCloudEvent(
            start_time=cloud_start_time,
            duration_minutes=cloud_duration_minutes,
            cloud_transmission_factor=cloud_transmission_factor,
        ),
        start_time=start_time,
        end_time=end_time,
    )
