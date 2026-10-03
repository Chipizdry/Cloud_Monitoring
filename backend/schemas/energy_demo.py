from enum import Enum
from typing import List

from pydantic import Field

from backend.schemas.base import BaseSchema


class DemoSolarBatteryScenario(str, Enum):
    WITH_BATTERY = "with_battery"
    WITHOUT_BATTERY = "without_battery"


class DemoInsolationProfile(str, Enum):
    IDEAL = "ideal"
    REAL = "real"


class DemoCompareDataSource(str, Enum):
    SYNTHETIC = "synthetic"
    MEASUREMENTS = "measurements"


class DemoEnergyObject(BaseSchema):
    id: str = Field(..., description="ID демо-объекта")
    name: str = Field(..., description="Название демо-объекта")
    solar_power_kw: float = Field(..., description="Установленная солнечная мощность, kW")
    battery_capacity_kwh: float = Field(..., description="Емкость АКБ, kWh")


class DemoInsolationPoint(BaseSchema):
    time: str = Field(..., description="Локальное время Europe/Kyiv, HH:MM")
    solar_radiation_w_m2: float = Field(..., ge=0, description="Солнечная радиация, W/m2")


class DemoCloudEvent(BaseSchema):
    start_time: str = Field(..., description="Время начала облака в Europe/Kyiv, HH:MM")
    duration_minutes: int = Field(..., ge=1, description="Длительность облака, минуты")
    cloud_transmission_factor: float = Field(
        ...,
        ge=0,
        le=1,
        description="Коэффициент пропускания облака. 1 значит без потерь, 0.5 значит проходит 50% базовой радиации.",
    )


class DemoBatteryConfig(BaseSchema):
    capacity_duration_minutes: float = Field(
        ...,
        gt=0,
        description="Емкость АКБ как минуты работы на пиковой мощности объекта",
    )
    initial_soc_percent: float = Field(
        ...,
        ge=0,
        le=100,
        description="Начальный SOC АКБ, проценты",
    )
    min_soc_percent: float = Field(
        ...,
        ge=0,
        le=100,
        description="Минимально допустимый SOC АКБ, проценты",
    )
    max_discharge_power_kw: float | None = Field(
        None,
        ge=0,
        description="Максимальная мощность разряда АКБ на объект, kW. Эффективное значение не ниже пиковой мощности объекта.",
    )
    max_charge_power_kw: float | None = Field(
        None,
        ge=0,
        description="Максимальная мощность заряда АКБ на объект, kW. Эффективное значение не ниже пиковой мощности объекта.",
    )
    spike_threshold_percent: float = Field(
        ...,
        ge=0,
        description="Порог резкого минутного изменения отдачи, проценты",
    )
    drop_compensation_duration_minutes: int = Field(
        ...,
        ge=1,
        description="Как долго компенсировать найденную просадку отдачи, минуты",
    )


class DemoObjectSimulationPoint(BaseSchema):
    time: str
    insolation_factor: float
    target_solar_radiation_w_m2: float
    solar_radiation_w_m2: float
    target_generation_kw: float
    solar_generation_kw: float
    battery_output_kw: float
    total_output_kw: float
    battery_soc_percent: float
    battery_energy_kwh: float


class DemoObjectSimulation(BaseSchema):
    id: str
    name: str
    solar_power_kw: float
    battery_capacity_kwh: float
    timeline: List[DemoObjectSimulationPoint]


class DemoObjectGenerationPoint(BaseSchema):
    time: str
    target_generation_kw: float | None = None
    solar_generation_kw: float
    with_battery_total_output_kw: float | None = None
    battery_power_kw: float | None = None
    battery_discharge_kw: float | None = None
    battery_charge_kw: float | None = None
    curtailed_power_kw: float | None = None
    battery_soc_percent: float | None = None


class DemoObjectGenerationTimeline(BaseSchema):
    id: str
    name: str
    solar_power_kw: float
    battery_capacity_kwh: float
    source_object_name: str | None = None
    source_date: str | None = None
    timeline: List[DemoObjectGenerationPoint]


class DemoGridSimulationPoint(BaseSchema):
    time: str
    insolation_factor: float
    target_solar_radiation_w_m2: float
    solar_radiation_w_m2: float
    target_power_kw: float
    solar_generation_kw: float
    battery_output_kw: float
    total_output_kw: float
    drop_kw: float
    battery_soc_percent: float


class DemoGridComparisonPoint(BaseSchema):
    time: str
    target_solar_radiation_w_m2: float | None = None
    solar_radiation_w_m2: float | None = None
    target_power_kw: float
    solar_generation_kw: float
    with_battery_total_output_kw: float
    without_battery_total_output_kw: float
    battery_output_kw: float
    battery_charge_kw: float = 0
    battery_power_kw: float | None = None
    curtailed_power_kw: float = 0
    with_battery_drop_kw: float
    without_battery_drop_kw: float
    battery_soc_percent: float


class DemoSolarBatterySummary(BaseSchema):
    installed_power_kw: float
    battery_capacity_kwh: float
    max_drop_kw: float
    battery_energy_used_kwh: float
    battery_energy_charged_kwh: float = 0
    min_battery_soc_percent: float


class DemoSolarBatterySimulationResponse(BaseSchema):
    timezone: str
    data_interval_minutes: int
    cloud_event: DemoCloudEvent
    insolation_profile: DemoInsolationProfile
    scenario: DemoSolarBatteryScenario
    cloud_enabled: bool
    battery_enabled: bool
    objects: List[DemoObjectSimulation]
    grid_timeline: List[DemoGridSimulationPoint]
    summary: DemoSolarBatterySummary


class DemoSolarBatteryComparisonResponse(BaseSchema):
    timezone: str
    data_interval_minutes: int
    data_source: DemoCompareDataSource
    range_start_time: str
    range_end_time: str
    battery_config: DemoBatteryConfig | None = None
    cloud_event: DemoCloudEvent | None = None
    insolation_profile: DemoInsolationProfile | None = None
    cloud_enabled: bool | None = None
    objects: List[DemoEnergyObject]
    object_generation_timelines: List[DemoObjectGenerationTimeline]
    grid_timeline: List[DemoGridComparisonPoint]
    with_battery_summary: DemoSolarBatterySummary
    without_battery_summary: DemoSolarBatterySummary


class DemoSolarBatteryConfigResponse(BaseSchema):
    timezone: str
    data_interval_minutes: int
    cloud_event: DemoCloudEvent
    insolation_profile: DemoInsolationProfile
    objects: List[DemoEnergyObject]
    insolation: List[DemoInsolationPoint]
