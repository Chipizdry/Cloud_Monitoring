from datetime import datetime

from pydantic import Field

from backend.schemas.base import BaseSchema


class DemoSolarBatteryV2ObjectBase(BaseSchema):
    name: str = Field(..., min_length=1, description="Название объекта")
    peak_power_kw: float = Field(..., gt=0, description="Пиковая мощность объекта, kW")
    battery_capacity_kwh: float = Field(..., gt=0, description="Емкость АКБ объекта, kWh")


class DemoSolarBatteryV2ObjectCreate(DemoSolarBatteryV2ObjectBase):
    pass


class DemoSolarBatteryV2ObjectUpdate(BaseSchema):
    name: str | None = Field(None, min_length=1, description="Название объекта")
    peak_power_kw: float | None = Field(None, gt=0, description="Пиковая мощность объекта, kW")
    battery_capacity_kwh: float | None = Field(None, gt=0, description="Емкость АКБ объекта, kWh")


class DemoSolarBatteryV2ObjectResponse(DemoSolarBatteryV2ObjectBase):
    id: str
    created_at: datetime
    updated_at: datetime


class DemoSolarBatteryV2AlgorithmBase(BaseSchema):
    support_peak_minutes: int = Field(
        ...,
        ge=1,
        le=120,
        description="Сколько минут АКБ должна держать пиковую мощность объекта",
    )
    correction_threshold_percent: float = Field(
        10,
        ge=0,
        le=100,
        description="Допустимое изменение отдачи за период, %",
    )
    min_soc_percent: float = Field(
        20,
        ge=0,
        lt=100,
        description="Минимальный SOC АКБ, %",
    )
    recalculation_period_minutes: int = Field(
        16,
        ge=1,
        le=120,
        description="Период пересчета алгоритма, минуты",
    )


class DemoSolarBatteryV2AlgorithmCreate(DemoSolarBatteryV2AlgorithmBase):
    pass


class DemoSolarBatteryV2AlgorithmUpdate(BaseSchema):
    support_peak_minutes: int | None = Field(
        None,
        ge=1,
        le=120,
        description="Сколько минут АКБ должна держать пиковую мощность объекта",
    )
    correction_threshold_percent: float | None = Field(
        None,
        ge=0,
        le=100,
        description="Допустимое изменение отдачи за период, %",
    )
    min_soc_percent: float | None = Field(
        None,
        ge=0,
        lt=100,
        description="Минимальный SOC АКБ, %",
    )
    recalculation_period_minutes: int | None = Field(
        None,
        ge=1,
        le=120,
        description="Период пересчета алгоритма, минуты",
    )


class DemoSolarBatteryV2AlgorithmResponse(DemoSolarBatteryV2AlgorithmBase):
    id: str
    object_id: str
    created_at: datetime
    updated_at: datetime


class DemoSolarBatteryV2GlobalAlgorithmBase(BaseSchema):
    external_battery_capacity_kwh: float = Field(
        ...,
        gt=0,
        description="Емкость внешней АКБ, kWh",
    )
    support_power_kw: float | None = Field(
        None,
        gt=0,
        description="Заданная мощность поддержки внешней АКБ, kW",
    )
    support_peak_minutes: int = Field(
        16,
        ge=1,
        le=120,
        description="Сколько минут внешняя АКБ должна держать заданную мощность",
    )
    correction_threshold_percent: float = Field(
        10,
        ge=0,
        le=100,
        description="Допустимое изменение общей отдачи за период, %",
    )
    min_soc_percent: float = Field(
        20,
        ge=0,
        lt=100,
        description="Минимальный SOC внешней АКБ, %",
    )
    recalculation_period_minutes: int = Field(
        16,
        ge=1,
        le=120,
        description="Период пересчета глобального алгоритма, минуты",
    )


class DemoSolarBatteryV2GlobalAlgorithmCreate(DemoSolarBatteryV2GlobalAlgorithmBase):
    pass


class DemoSolarBatteryV2GlobalAlgorithmUpdate(BaseSchema):
    external_battery_capacity_kwh: float | None = Field(
        None,
        gt=0,
        description="Емкость внешней АКБ, kWh",
    )
    support_power_kw: float | None = Field(
        None,
        gt=0,
        description="Заданная мощность поддержки внешней АКБ, kW",
    )
    support_peak_minutes: int | None = Field(
        None,
        ge=1,
        le=120,
        description="Сколько минут внешняя АКБ должна держать заданную мощность",
    )
    correction_threshold_percent: float | None = Field(
        None,
        ge=0,
        le=100,
        description="Допустимое изменение общей отдачи за период, %",
    )
    min_soc_percent: float | None = Field(
        None,
        ge=0,
        lt=100,
        description="Минимальный SOC внешней АКБ, %",
    )
    recalculation_period_minutes: int | None = Field(
        None,
        ge=1,
        le=120,
        description="Период пересчета глобального алгоритма, минуты",
    )


class DemoSolarBatteryV2GlobalAlgorithmResponse(DemoSolarBatteryV2GlobalAlgorithmBase):
    id: str
    name: str
    created_at: datetime
    updated_at: datetime


class DemoSolarBatteryV2ObjectPoint(BaseSchema):
    time: str
    without_correction_kw: float
    with_correction_kw: float
    target_output_kw: float
    battery_power_kw: float
    battery_charge_kw: float
    battery_discharge_kw: float
    curtailed_power_kw: float
    battery_soc_percent: float


class DemoSolarBatteryV2ObjectTimeline(BaseSchema):
    id: str
    name: str
    peak_power_kw: float
    battery_capacity_kwh: float
    source_object_name: str
    source_date: str
    algorithm: DemoSolarBatteryV2AlgorithmResponse | None
    timeline: list[DemoSolarBatteryV2ObjectPoint]


class DemoSolarBatteryV2GridPoint(BaseSchema):
    time: str
    without_correction_kw: float
    after_object_correction_kw: float
    with_global_correction_kw: float
    global_target_output_kw: float
    global_battery_power_kw: float
    global_battery_charge_kw: float
    global_battery_discharge_kw: float
    global_curtailed_power_kw: float
    global_battery_soc_percent: float


class DemoSolarBatteryV2SimulationSummary(BaseSchema):
    objects_count: int
    installed_power_kw: float
    objects_battery_capacity_kwh: float
    external_battery_capacity_kwh: float | None
    data_points_per_timeline: int


class DemoSolarBatteryV2SimulationResponse(BaseSchema):
    timezone: str
    data_interval_minutes: int
    source_object_name: str
    range_start_time: str
    range_end_time: str
    object_timelines: list[DemoSolarBatteryV2ObjectTimeline]
    grid_timeline: list[DemoSolarBatteryV2GridPoint]
    global_algorithm: DemoSolarBatteryV2GlobalAlgorithmResponse | None
    summary: DemoSolarBatteryV2SimulationSummary

