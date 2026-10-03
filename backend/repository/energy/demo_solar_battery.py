from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Sequence, TypeVar
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import CerboMeasurement
from backend.schemas.energy_demo import (
    DemoBatteryConfig,
    DemoCloudEvent,
    DemoCompareDataSource,
    DemoEnergyObject,
    DemoGridComparisonPoint,
    DemoGridSimulationPoint,
    DemoInsolationProfile,
    DemoInsolationPoint,
    DemoObjectGenerationPoint,
    DemoObjectGenerationTimeline,
    DemoObjectSimulation,
    DemoObjectSimulationPoint,
    DemoSolarBatteryComparisonResponse,
    DemoSolarBatteryConfigResponse,
    DemoSolarBatteryScenario,
    DemoSolarBatterySimulationResponse,
    DemoSolarBatterySummary,
)


T = TypeVar("T")

# Таймзона для подписей времени и выбора дат измерений.
DEMO_TIMEZONE = "Europe/Kyiv"

# Дефолтное начало временного диапазона для графиков.
DEFAULT_RANGE_START_TIME = "00:00"

# Дефолтный конец временного диапазона для графиков.
DEFAULT_RANGE_END_TIME = "23:59"

# Количество минут в одном часе для расчетов времени и энергии.
MINUTES_PER_HOUR = 60

# Количество часов в сутках для расчета полного дневного таймлайна.
HOURS_PER_DAY = 24

# Количество минут в сутках для построения полного дневного таймлайна.
DAY_MINUTES = HOURS_PER_DAY * MINUTES_PER_HOUR

# Делитель для перевода W в kW.
WATTS_PER_KILOWATT = 1000

# Количество знаков после запятой для числовых значений в ответе.
OUTPUT_ROUND_DIGITS = 3

# Шаг симуляции. Compare сейчас отдает одну точку на каждую минуту.
DATA_INTERVAL_MINUTES = 1

# Шаг симуляции в часах для пересчета kW в kWh.
DATA_INTERVAL_HOURS = DATA_INTERVAL_MINUTES / MINUTES_PER_HOUR

# Делитель для перевода процентов в доли.
PERCENT_DIVISOR = 100

# Начальный SOC для старого синтетического симулятора.
INITIAL_BATTERY_SOC_PERCENT = 100.0

# Референс солнечной радиации для старого синтетического симулятора.
REFERENCE_SOLAR_RADIATION_W_M2 = 1000

# Пропускание без искусственной тучи.
FULL_CLOUD_TRANSMISSION_FACTOR = 1.0

# Реальная станция, измерения которой используем как солнечный профиль.
MEASUREMENTS_SOURCE_OBJECT_NAME = "COR-AZK"

# Сдвиги дней для четырех виртуальных объектов: вчера, позавчера и дальше.
MEASUREMENTS_SOURCE_DAYS_BACK: tuple[int, ...] = (1, 2, 3, 4)

# Пиковая мощность COR-AZK для нормализации измерений в профиль 0..1.
MEASUREMENTS_PROFILE_REFERENCE_POWER_KW = 150

# Нижняя граница нормализованного солнечного профиля.
MEASUREMENTS_MIN_PROFILE_FACTOR = 0

# Верхняя граница нормализованного солнечного профиля.
MEASUREMENTS_MAX_PROFILE_FACTOR = 1

# Емкость виртуальной АКБ: минуты работы на пиковой мощности объекта.
MEASUREMENTS_DEFAULT_BATTERY_DURATION_MINUTES = 60

# Начальный SOC каждой виртуальной АКБ в начале дня.
MEASUREMENTS_DEFAULT_INITIAL_SOC_PERCENT = 100

# Минимальный SOC. Ниже этого уровня разряд АКБ запрещен.
MEASUREMENTS_DEFAULT_MIN_SOC_PERCENT = 20

# Максимальная мощность разряда. None значит брать пик конкретного объекта.
MEASUREMENTS_DEFAULT_MAX_DISCHARGE_POWER_KW: float | None = None

# Максимальная мощность заряда. None значит брать пик конкретного объекта.
MEASUREMENTS_DEFAULT_MAX_CHARGE_POWER_KW: float | None = None

# Допустимое минутное изменение отдачи в сеть до включения коррекции.
MEASUREMENTS_DEFAULT_SPIKE_THRESHOLD_PERCENT = 10

# Длительность плавной целевой траектории после резкой просадки.
MEASUREMENTS_DEFAULT_DROP_COMPENSATION_DURATION_MINUTES = 15

# Минимальная отдача для включения ramp-коррекции, чтобы не резать рассвет.
MEASUREMENTS_RAMP_CONTROL_MIN_POWER_FACTOR = 0.1

# Минимальное количество минутных шагов для траектории компенсации.
MEASUREMENTS_MIN_DROP_COMPENSATION_STEPS = 1

# Виртуальные объекты: id, имя, пиковая отдача в сеть в kW.
MEASUREMENTS_DEMO_OBJECT_SPECS: tuple[tuple[str, str, float], ...] = (
    ("measurements-object-1", "Объект 1", 2750),
    ("measurements-object-2", "Объект 2", 560),
    ("measurements-object-3", "Объект 3", 256),
    ("measurements-object-4", "Объект 4", 64),
)

# Дефолтная модель АКБ и коррекции для /compare.
MEASUREMENTS_DEFAULT_BATTERY_CONFIG = DemoBatteryConfig(
    capacity_duration_minutes=MEASUREMENTS_DEFAULT_BATTERY_DURATION_MINUTES,
    initial_soc_percent=MEASUREMENTS_DEFAULT_INITIAL_SOC_PERCENT,
    min_soc_percent=MEASUREMENTS_DEFAULT_MIN_SOC_PERCENT,
    max_discharge_power_kw=MEASUREMENTS_DEFAULT_MAX_DISCHARGE_POWER_KW,
    max_charge_power_kw=MEASUREMENTS_DEFAULT_MAX_CHARGE_POWER_KW,
    spike_threshold_percent=MEASUREMENTS_DEFAULT_SPIKE_THRESHOLD_PERCENT,
    drop_compensation_duration_minutes=MEASUREMENTS_DEFAULT_DROP_COMPENSATION_DURATION_MINUTES,
)

# Время начала старого искусственного облака.
DEMO_CLOUD_START_TIME = "13:00"

# Длительность старого искусственного облака в минутах.
DEMO_CLOUD_DURATION_MINUTES = 15

# Коэффициент пропускания старого искусственного облака.
DEMO_CLOUD_TRANSMISSION_FACTOR = 0.48

# Старое искусственное облако. Сейчас /compare его игнорирует, оставлено для совместимости.
DEMO_CLOUD_EVENT = DemoCloudEvent(
    start_time=DEMO_CLOUD_START_TIME,
    duration_minutes=DEMO_CLOUD_DURATION_MINUTES,
    cloud_transmission_factor=DEMO_CLOUD_TRANSMISSION_FACTOR,
)


@dataclass(frozen=True)
class DemoObjectConfig:
    id: str
    name: str
    solar_power_kw: float
    battery_capacity_kwh: float


@dataclass(frozen=True)
class MeasurementBatterySimulation:
    target_values: list[float]
    with_battery_values: list[float]
    battery_power_values: list[float]
    battery_discharge_values: list[float]
    battery_charge_values: list[float]
    curtailed_power_values: list[float]
    battery_soc_values: list[float]
    battery_energy_used_kwh: float
    battery_energy_charged_kwh: float
    min_battery_soc_percent: float


def _get_battery_capacity_kwh(
    *,
    solar_power_kw: float,
    capacity_duration_minutes: float,
) -> float:
    return solar_power_kw * capacity_duration_minutes / MINUTES_PER_HOUR


# Конфиги виртуальных объектов, собранные из MEASUREMENTS_DEMO_OBJECT_SPECS.
MEASUREMENTS_DEMO_OBJECTS: tuple[DemoObjectConfig, ...] = tuple(
    DemoObjectConfig(
        id=obj_id,
        name=obj_name,
        solar_power_kw=solar_power_kw,
        battery_capacity_kwh=_get_battery_capacity_kwh(
            solar_power_kw=solar_power_kw,
            capacity_duration_minutes=MEASUREMENTS_DEFAULT_BATTERY_DURATION_MINUTES,
        ),
    )
    for obj_id, obj_name, solar_power_kw in MEASUREMENTS_DEMO_OBJECT_SPECS
)


# Старые синтетические объекты для /config и старого симулятора.
DEMO_OBJECTS: tuple[DemoObjectConfig, ...] = (
    DemoObjectConfig(
        id="demo-object-1",
        name="Объект 1",
        solar_power_kw=2500,
        battery_capacity_kwh=625,
    ),
    DemoObjectConfig(
        id="demo-object-2",
        name="Объект 2",
        solar_power_kw=150,
        battery_capacity_kwh=500,
    ),
    DemoObjectConfig(
        id="demo-object-3",
        name="Объект 3",
        solar_power_kw=1500,
        battery_capacity_kwh=350,
    ),
    DemoObjectConfig(
        id="demo-object-4",
        name="Объект 4",
        solar_power_kw=1000,
        battery_capacity_kwh=250,
    ),
)

# Идеальный синтетический профиль инсоляции для демонстрации без реальных измерений.
DEMO_IDEAL_INSOLATION: tuple[DemoInsolationPoint, ...] = (
    DemoInsolationPoint(time="00:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="01:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="02:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="03:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="04:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="05:00", solar_radiation_w_m2=30),
    DemoInsolationPoint(time="06:00", solar_radiation_w_m2=120),
    DemoInsolationPoint(time="07:00", solar_radiation_w_m2=280),
    DemoInsolationPoint(time="08:00", solar_radiation_w_m2=450),
    DemoInsolationPoint(time="09:00", solar_radiation_w_m2=620),
    DemoInsolationPoint(time="10:00", solar_radiation_w_m2=780),
    DemoInsolationPoint(time="11:00", solar_radiation_w_m2=900),
    DemoInsolationPoint(time="12:00", solar_radiation_w_m2=960),
    DemoInsolationPoint(time="13:00", solar_radiation_w_m2=940),
    DemoInsolationPoint(time="14:00", solar_radiation_w_m2=840),
    DemoInsolationPoint(time="15:00", solar_radiation_w_m2=700),
    DemoInsolationPoint(time="16:00", solar_radiation_w_m2=520),
    DemoInsolationPoint(time="17:00", solar_radiation_w_m2=340),
    DemoInsolationPoint(time="18:00", solar_radiation_w_m2=180),
    DemoInsolationPoint(time="19:00", solar_radiation_w_m2=70),
    DemoInsolationPoint(time="20:00", solar_radiation_w_m2=10),
    DemoInsolationPoint(time="21:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="22:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="23:00", solar_radiation_w_m2=0),
)

# Реальный почасовой профиль солнечной радиации для Киева за 10 июля.
# Единицы измерения: W/m2.
DEMO_REAL_KYIV_INSOLATION: tuple[DemoInsolationPoint, ...] = (
    DemoInsolationPoint(time="00:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="01:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="02:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="03:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="04:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="05:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="06:00", solar_radiation_w_m2=13),
    DemoInsolationPoint(time="07:00", solar_radiation_w_m2=145),
    DemoInsolationPoint(time="08:00", solar_radiation_w_m2=293),
    DemoInsolationPoint(time="09:00", solar_radiation_w_m2=275),
    DemoInsolationPoint(time="10:00", solar_radiation_w_m2=163),
    DemoInsolationPoint(time="11:00", solar_radiation_w_m2=190),
    DemoInsolationPoint(time="12:00", solar_radiation_w_m2=378),
    DemoInsolationPoint(time="13:00", solar_radiation_w_m2=335),
    DemoInsolationPoint(time="14:00", solar_radiation_w_m2=309),
    DemoInsolationPoint(time="15:00", solar_radiation_w_m2=319),
    DemoInsolationPoint(time="16:00", solar_radiation_w_m2=183),
    DemoInsolationPoint(time="17:00", solar_radiation_w_m2=159),
    DemoInsolationPoint(time="18:00", solar_radiation_w_m2=94),
    DemoInsolationPoint(time="19:00", solar_radiation_w_m2=53),
    DemoInsolationPoint(time="20:00", solar_radiation_w_m2=16),
    DemoInsolationPoint(time="21:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="22:00", solar_radiation_w_m2=0),
    DemoInsolationPoint(time="23:00", solar_radiation_w_m2=0),
)


def _get_insolation_profile(
    insolation_profile: DemoInsolationProfile,
) -> tuple[DemoInsolationPoint, ...]:
    if insolation_profile == DemoInsolationProfile.REAL:
        return DEMO_REAL_KYIV_INSOLATION
    return DEMO_IDEAL_INSOLATION


def _time_to_minutes(value: str) -> int:
    hours, minutes = value.split(":", 1)
    return int(hours) * MINUTES_PER_HOUR + int(minutes)


def _minutes_to_time(minutes: int) -> str:
    minutes %= DAY_MINUTES
    return f"{minutes // MINUTES_PER_HOUR:02d}:{minutes % MINUTES_PER_HOUR:02d}"


def _interpolate_insolation_profile(
    source_profile: Sequence[DemoInsolationPoint],
    *,
    interval_minutes: int = DATA_INTERVAL_MINUTES,
) -> list[DemoInsolationPoint]:
    points_by_minute = {
        _time_to_minutes(point.time): point.solar_radiation_w_m2
        for point in source_profile
    }
    source_minutes = sorted(points_by_minute)
    if not source_minutes:
        return []

    result: list[DemoInsolationPoint] = []
    for minute in range(0, DAY_MINUTES, interval_minutes):
        previous_minute = max(
            (m for m in source_minutes if m <= minute),
            default=source_minutes[-1] - DAY_MINUTES,
        )
        next_candidates = [m for m in source_minutes if m > minute]
        next_minute = next_candidates[0] if next_candidates else source_minutes[0] + DAY_MINUTES

        previous_value = points_by_minute[previous_minute % DAY_MINUTES]
        next_value = points_by_minute[next_minute % DAY_MINUTES]
        span = next_minute - previous_minute
        ratio = (minute - previous_minute) / span if span else 0
        solar_radiation_w_m2 = previous_value + (next_value - previous_value) * ratio
        result.append(
            DemoInsolationPoint(
                time=_minutes_to_time(minute),
                solar_radiation_w_m2=round(
                    solar_radiation_w_m2,
                    OUTPUT_ROUND_DIGITS,
                ),
            )
        )
    return result


def _get_cloud_transmission_for_point(
    point_time: str,
    *,
    cloud_enabled: bool,
    cloud_event: DemoCloudEvent,
) -> float:
    if not cloud_enabled:
        return FULL_CLOUD_TRANSMISSION_FACTOR

    point_minutes = _time_to_minutes(point_time)
    start_minutes = _time_to_minutes(cloud_event.start_time)
    end_minutes = start_minutes + cloud_event.duration_minutes
    point_end_minutes = point_minutes + DATA_INTERVAL_MINUTES

    if point_minutes < end_minutes and point_end_minutes > start_minutes:
        return cloud_event.cloud_transmission_factor
    return FULL_CLOUD_TRANSMISSION_FACTOR


def _is_time_in_range(time: str, start_time: str, end_time: str) -> bool:
    time_minutes = _time_to_minutes(time)
    start_minutes = _time_to_minutes(start_time)
    end_minutes = _time_to_minutes(end_time)

    if start_minutes <= end_minutes:
        return start_minutes <= time_minutes <= end_minutes
    return time_minutes >= start_minutes or time_minutes <= end_minutes


def _filter_timeline_by_range(
    timeline: Sequence[T],
    *,
    start_time: str,
    end_time: str,
) -> list[T]:
    return [
        point
        for point in timeline
        if _is_time_in_range(point.time, start_time, end_time)
    ]


def get_demo_solar_battery_config(
    insolation_profile: DemoInsolationProfile = DemoInsolationProfile.IDEAL,
) -> DemoSolarBatteryConfigResponse:
    profile = _interpolate_insolation_profile(_get_insolation_profile(insolation_profile))
    return DemoSolarBatteryConfigResponse(
        timezone=DEMO_TIMEZONE,
        data_interval_minutes=DATA_INTERVAL_MINUTES,
        cloud_event=DEMO_CLOUD_EVENT,
        insolation_profile=insolation_profile,
        objects=[
            DemoEnergyObject(
                id=obj.id,
                name=obj.name,
                solar_power_kw=obj.solar_power_kw,
                battery_capacity_kwh=obj.battery_capacity_kwh,
            )
            for obj in DEMO_OBJECTS
        ],
        insolation=profile,
    )


def simulate_demo_solar_battery(
    *,
    scenario: DemoSolarBatteryScenario = DemoSolarBatteryScenario.WITH_BATTERY,
    insolation_profile: DemoInsolationProfile = DemoInsolationProfile.IDEAL,
    cloud_enabled: bool = True,
    battery_enabled: bool | None = None,
    cloud_event: DemoCloudEvent = DEMO_CLOUD_EVENT,
    insolation: Sequence[DemoInsolationPoint] | None = None,
) -> DemoSolarBatterySimulationResponse:
    profile = list(
        insolation
        or _interpolate_insolation_profile(_get_insolation_profile(insolation_profile))
    )
    effective_battery_enabled = (
        scenario == DemoSolarBatteryScenario.WITH_BATTERY
        if battery_enabled is None
        else battery_enabled
    )
    effective_scenario = (
        DemoSolarBatteryScenario.WITH_BATTERY
        if effective_battery_enabled
        else DemoSolarBatteryScenario.WITHOUT_BATTERY
    )
    object_states = {
        obj.id: obj.battery_capacity_kwh * INITIAL_BATTERY_SOC_PERCENT / PERCENT_DIVISOR
        for obj in DEMO_OBJECTS
    }
    object_timelines: dict[str, list[DemoObjectSimulationPoint]] = {
        obj.id: [] for obj in DEMO_OBJECTS
    }
    grid_timeline: list[DemoGridSimulationPoint] = []

    for point in profile:
        grid_target_power_kw = 0.0
        grid_solar_generation_kw = 0.0
        grid_battery_output_kw = 0.0
        grid_total_output_kw = 0.0
        grid_battery_energy_kwh = 0.0
        total_battery_capacity_kwh = sum(obj.battery_capacity_kwh for obj in DEMO_OBJECTS)

        for obj in DEMO_OBJECTS:
            cloud_transmission_factor = _get_cloud_transmission_for_point(
                point.time,
                cloud_enabled=cloud_enabled,
                cloud_event=cloud_event,
            )
            target_solar_radiation_w_m2 = point.solar_radiation_w_m2
            solar_radiation_w_m2 = (
                point.solar_radiation_w_m2 * cloud_transmission_factor
            )
            target_insolation_factor = (
                target_solar_radiation_w_m2 / REFERENCE_SOLAR_RADIATION_W_M2
            )
            insolation_factor = solar_radiation_w_m2 / REFERENCE_SOLAR_RADIATION_W_M2
            target_generation_kw = obj.solar_power_kw * target_insolation_factor
            solar_generation_kw = obj.solar_power_kw * insolation_factor
            requested_battery_kw = max(target_generation_kw - solar_generation_kw, 0)

            available_energy_kwh = object_states[obj.id]
            battery_output_kw = 0.0
            if effective_battery_enabled:
                battery_output_kw = min(
                    requested_battery_kw,
                    available_energy_kwh / DATA_INTERVAL_HOURS,
                )

            used_energy_kwh = battery_output_kw * DATA_INTERVAL_HOURS
            object_states[obj.id] = max(available_energy_kwh - used_energy_kwh, 0)
            battery_soc_percent = (
                object_states[obj.id] / obj.battery_capacity_kwh * PERCENT_DIVISOR
                if obj.battery_capacity_kwh
                else 0
            )
            total_output_kw = solar_generation_kw + battery_output_kw

            object_timelines[obj.id].append(
                DemoObjectSimulationPoint(
                    time=point.time,
                    insolation_factor=round(insolation_factor, OUTPUT_ROUND_DIGITS),
                    target_solar_radiation_w_m2=round(
                        target_solar_radiation_w_m2,
                        OUTPUT_ROUND_DIGITS,
                    ),
                    solar_radiation_w_m2=round(solar_radiation_w_m2, OUTPUT_ROUND_DIGITS),
                    target_generation_kw=round(target_generation_kw, OUTPUT_ROUND_DIGITS),
                    solar_generation_kw=round(solar_generation_kw, OUTPUT_ROUND_DIGITS),
                    battery_output_kw=round(battery_output_kw, OUTPUT_ROUND_DIGITS),
                    total_output_kw=round(total_output_kw, OUTPUT_ROUND_DIGITS),
                    battery_soc_percent=round(battery_soc_percent, OUTPUT_ROUND_DIGITS),
                    battery_energy_kwh=round(object_states[obj.id], OUTPUT_ROUND_DIGITS),
                )
            )

            grid_target_power_kw += target_generation_kw
            grid_solar_generation_kw += solar_generation_kw
            grid_battery_output_kw += battery_output_kw
            grid_total_output_kw += total_output_kw
            grid_battery_energy_kwh += object_states[obj.id]

        average_soc_percent = (
            grid_battery_energy_kwh / total_battery_capacity_kwh * PERCENT_DIVISOR
            if total_battery_capacity_kwh
            else 0
        )
        grid_timeline.append(
            DemoGridSimulationPoint(
                time=point.time,
                insolation_factor=round(
                    point.solar_radiation_w_m2
                    * _get_cloud_transmission_for_point(
                        point.time,
                        cloud_enabled=cloud_enabled,
                        cloud_event=cloud_event,
                    )
                    / REFERENCE_SOLAR_RADIATION_W_M2,
                    OUTPUT_ROUND_DIGITS,
                ),
                target_solar_radiation_w_m2=round(
                    point.solar_radiation_w_m2,
                    OUTPUT_ROUND_DIGITS,
                ),
                solar_radiation_w_m2=round(
                    point.solar_radiation_w_m2
                    * _get_cloud_transmission_for_point(
                        point.time,
                        cloud_enabled=cloud_enabled,
                        cloud_event=cloud_event,
                    ),
                    OUTPUT_ROUND_DIGITS,
                ),
                target_power_kw=round(grid_target_power_kw, OUTPUT_ROUND_DIGITS),
                solar_generation_kw=round(grid_solar_generation_kw, OUTPUT_ROUND_DIGITS),
                battery_output_kw=round(grid_battery_output_kw, OUTPUT_ROUND_DIGITS),
                total_output_kw=round(grid_total_output_kw, OUTPUT_ROUND_DIGITS),
                drop_kw=round(
                    max(grid_target_power_kw - grid_total_output_kw, 0),
                    OUTPUT_ROUND_DIGITS,
                ),
                battery_soc_percent=round(average_soc_percent, OUTPUT_ROUND_DIGITS),
            )
        )

    objects = [
        DemoObjectSimulation(
            id=obj.id,
            name=obj.name,
            solar_power_kw=obj.solar_power_kw,
            battery_capacity_kwh=obj.battery_capacity_kwh,
            timeline=object_timelines[obj.id],
        )
        for obj in DEMO_OBJECTS
    ]

    initial_battery_energy_kwh = sum(obj.battery_capacity_kwh for obj in DEMO_OBJECTS)
    remaining_battery_energy_kwh = sum(object_states.values())
    min_grid_soc_percent = min(
        (point.battery_soc_percent for point in grid_timeline),
        default=INITIAL_BATTERY_SOC_PERCENT,
    )

    return DemoSolarBatterySimulationResponse(
        timezone=DEMO_TIMEZONE,
        data_interval_minutes=DATA_INTERVAL_MINUTES,
        cloud_event=cloud_event,
        insolation_profile=insolation_profile,
        scenario=effective_scenario,
        cloud_enabled=cloud_enabled,
        battery_enabled=effective_battery_enabled,
        objects=objects,
        grid_timeline=grid_timeline,
        summary=DemoSolarBatterySummary(
            installed_power_kw=sum(obj.solar_power_kw for obj in DEMO_OBJECTS),
            battery_capacity_kwh=initial_battery_energy_kwh,
            max_drop_kw=max((point.drop_kw for point in grid_timeline), default=0),
            battery_energy_used_kwh=round(
                initial_battery_energy_kwh - remaining_battery_energy_kwh,
                OUTPUT_ROUND_DIGITS,
            ),
            min_battery_soc_percent=round(min_grid_soc_percent, OUTPUT_ROUND_DIGITS),
        ),
    )


def compare_demo_solar_battery(
    *,
    insolation_profile: DemoInsolationProfile = DemoInsolationProfile.IDEAL,
    cloud_enabled: bool = True,
    cloud_event: DemoCloudEvent = DEMO_CLOUD_EVENT,
    start_time: str = DEFAULT_RANGE_START_TIME,
    end_time: str = DEFAULT_RANGE_END_TIME,
) -> DemoSolarBatteryComparisonResponse:
    with_battery = simulate_demo_solar_battery(
        insolation_profile=insolation_profile,
        cloud_enabled=cloud_enabled,
        battery_enabled=True,
        cloud_event=cloud_event,
    )
    without_battery = simulate_demo_solar_battery(
        insolation_profile=insolation_profile,
        cloud_enabled=cloud_enabled,
        battery_enabled=False,
        cloud_event=cloud_event,
    )

    grid_timeline = _filter_timeline_by_range(
        [
            DemoGridComparisonPoint(
                time=with_point.time,
                target_solar_radiation_w_m2=with_point.target_solar_radiation_w_m2,
                solar_radiation_w_m2=with_point.solar_radiation_w_m2,
                target_power_kw=with_point.target_power_kw,
                solar_generation_kw=with_point.solar_generation_kw,
                with_battery_total_output_kw=with_point.total_output_kw,
                without_battery_total_output_kw=without_point.total_output_kw,
                battery_output_kw=with_point.battery_output_kw,
                with_battery_drop_kw=with_point.drop_kw,
                without_battery_drop_kw=without_point.drop_kw,
                battery_soc_percent=with_point.battery_soc_percent,
            )
            for with_point, without_point in zip(
                with_battery.grid_timeline,
                without_battery.grid_timeline,
            )
        ],
        start_time=start_time,
        end_time=end_time,
    )

    object_generation_timelines = [
        DemoObjectGenerationTimeline(
            id=obj.id,
            name=obj.name,
            solar_power_kw=obj.solar_power_kw,
            battery_capacity_kwh=obj.battery_capacity_kwh,
            timeline=_filter_timeline_by_range(
                [
                    DemoObjectGenerationPoint(
                        time=point.time,
                        solar_generation_kw=point.solar_generation_kw,
                    )
                    for point in obj.timeline
                ],
                start_time=start_time,
                end_time=end_time,
            ),
        )
        for obj in without_battery.objects
    ]

    return DemoSolarBatteryComparisonResponse(
        timezone=DEMO_TIMEZONE,
        data_interval_minutes=DATA_INTERVAL_MINUTES,
        data_source=DemoCompareDataSource.SYNTHETIC,
        range_start_time=start_time,
        range_end_time=end_time,
        cloud_event=cloud_event,
        insolation_profile=insolation_profile,
        cloud_enabled=cloud_enabled,
        objects=[
            DemoEnergyObject(
                id=obj.id,
                name=obj.name,
                solar_power_kw=obj.solar_power_kw,
                battery_capacity_kwh=obj.battery_capacity_kwh,
            )
            for obj in DEMO_OBJECTS
        ],
        object_generation_timelines=object_generation_timelines,
        grid_timeline=grid_timeline,
        with_battery_summary=with_battery.summary,
        without_battery_summary=without_battery.summary,
    )


def _minute_labels() -> list[str]:
    return [_minutes_to_time(minute) for minute in range(DAY_MINUTES)]


async def _get_measurement_generation_by_minute(
    db: AsyncSession,
    *,
    source_date,
    source_object_name: str,
) -> dict[str, float]:
    start_dt = datetime.combine(source_date, time.min)
    end_dt = start_dt + timedelta(days=1)
    minute_bucket = func.date_trunc("minute", CerboMeasurement.measured_at).label("minute")

    stmt = (
        select(
            minute_bucket,
            func.avg(CerboMeasurement.solar_total_pv_power).label("solar_power_w"),
        )
        .where(
            CerboMeasurement.object_name == source_object_name,
            CerboMeasurement.measured_at >= start_dt,
            CerboMeasurement.measured_at < end_dt,
        )
        .group_by(minute_bucket)
        .order_by(minute_bucket)
    )

    result = await db.execute(stmt)
    values: dict[str, float] = {}
    for minute_dt, solar_power_w in result.all():
        if minute_dt is None or solar_power_w is None:
            continue
        values[minute_dt.strftime("%H:%M")] = max(
            round(float(solar_power_w) / WATTS_PER_KILOWATT, OUTPUT_ROUND_DIGITS),
            0,
        )
    return values


def _get_measurements_demo_objects(
    battery_config: DemoBatteryConfig,
) -> tuple[DemoObjectConfig, ...]:
    return tuple(
        DemoObjectConfig(
            id=obj.id,
            name=obj.name,
            solar_power_kw=obj.solar_power_kw,
            battery_capacity_kwh=_get_battery_capacity_kwh(
                solar_power_kw=obj.solar_power_kw,
                capacity_duration_minutes=battery_config.capacity_duration_minutes,
            ),
        )
        for obj in MEASUREMENTS_DEMO_OBJECTS
    )


def _apply_cloud_to_measurement_generation(
    actual_values: list[float],
    *,
    cloud_enabled: bool,
    cloud_event: DemoCloudEvent,
) -> list[float]:
    if not cloud_enabled:
        return list(actual_values)

    result = list(actual_values)
    start_idx = _time_to_minutes(cloud_event.start_time)
    affected_minutes = min(cloud_event.duration_minutes, len(result))

    for offset in range(affected_minutes):
        idx = (start_idx + offset) % len(result)
        result[idx] = round(
            result[idx] * cloud_event.cloud_transmission_factor,
            OUTPUT_ROUND_DIGITS,
        )

    return result


def _project_measurement_generation_to_object(
    station_generation_values: list[float],
    *,
    obj: DemoObjectConfig,
) -> list[float]:
    return [
        round(
            obj.solar_power_kw
            * min(
                max(
                    station_power_kw / MEASUREMENTS_PROFILE_REFERENCE_POWER_KW,
                    MEASUREMENTS_MIN_PROFILE_FACTOR,
                ),
                MEASUREMENTS_MAX_PROFILE_FACTOR,
            ),
            OUTPUT_ROUND_DIGITS,
        )
        for station_power_kw in station_generation_values
    ]


def _simulate_measurement_battery(
    actual_values: list[float],
    *,
    battery_config: DemoBatteryConfig,
    obj: DemoObjectConfig,
) -> MeasurementBatterySimulation:
    capacity_kwh = obj.battery_capacity_kwh
    battery_energy_kwh = (
        capacity_kwh * battery_config.initial_soc_percent / PERCENT_DIVISOR
    )
    min_battery_energy_kwh = (
        capacity_kwh * battery_config.min_soc_percent / PERCENT_DIVISOR
    )
    max_discharge_power_kw = max(
        battery_config.max_discharge_power_kw or obj.solar_power_kw,
        obj.solar_power_kw,
    )
    max_charge_power_kw = max(
        battery_config.max_charge_power_kw or obj.solar_power_kw,
        obj.solar_power_kw,
    )

    target_values: list[float] = []
    with_battery_values: list[float] = []
    battery_power_values: list[float] = []
    battery_discharge_values: list[float] = []
    battery_charge_values: list[float] = []
    curtailed_power_values: list[float] = []
    battery_soc_values: list[float] = []

    battery_energy_used_kwh = 0.0
    battery_energy_charged_kwh = 0.0
    min_battery_soc_percent = battery_config.initial_soc_percent
    max_minute_change_ratio = battery_config.spike_threshold_percent / PERCENT_DIVISOR
    ramp_control_min_power_kw = (
        obj.solar_power_kw * MEASUREMENTS_RAMP_CONTROL_MIN_POWER_FACTOR
    )
    drop_start_power_kw: float | None = None
    drop_end_power_kw = 0.0
    drop_elapsed_steps = 0
    drop_compensation_steps = max(
        round(battery_config.drop_compensation_duration_minutes / DATA_INTERVAL_MINUTES),
        MEASUREMENTS_MIN_DROP_COMPENSATION_STEPS,
    )

    for idx, actual_power_kw in enumerate(actual_values):
        target_power_kw = actual_power_kw
        battery_discharge_kw = 0.0
        battery_charge_kw = 0.0

        if (
            idx > 0
            and with_battery_values[-1] >= ramp_control_min_power_kw
        ):
            previous_output_kw = with_battery_values[-1]
            max_allowed_output_kw = previous_output_kw * (1 + max_minute_change_ratio)
            min_allowed_output_kw = previous_output_kw * (1 - max_minute_change_ratio)

            if drop_start_power_kw is not None:
                drop_elapsed_steps += 1
                drop_progress = min(
                    drop_elapsed_steps / drop_compensation_steps,
                    1,
                )
                drop_target_power_kw = (
                    drop_start_power_kw
                    + (drop_end_power_kw - drop_start_power_kw) * drop_progress
                )

                if actual_power_kw < drop_target_power_kw and drop_progress < 1:
                    target_power_kw = drop_target_power_kw
                else:
                    drop_start_power_kw = None
                    drop_elapsed_steps = 0

            if drop_start_power_kw is None:
                if actual_power_kw < min_allowed_output_kw:
                    drop_start_power_kw = previous_output_kw
                    drop_end_power_kw = actual_power_kw
                    drop_elapsed_steps = 1
                    drop_progress = min(
                        drop_elapsed_steps / drop_compensation_steps,
                        1,
                    )
                    target_power_kw = (
                        drop_start_power_kw
                        + (drop_end_power_kw - drop_start_power_kw) * drop_progress
                    )
                elif actual_power_kw > max_allowed_output_kw:
                    target_power_kw = max_allowed_output_kw

            if actual_power_kw > target_power_kw:
                requested_charge_kw = actual_power_kw - target_power_kw
                available_charge_kw = max(
                    (capacity_kwh - battery_energy_kwh) / DATA_INTERVAL_HOURS,
                    0,
                )
                battery_charge_kw = min(
                    requested_charge_kw,
                    max_charge_power_kw,
                    available_charge_kw,
                )
                charged_energy_kwh = battery_charge_kw * DATA_INTERVAL_HOURS
                battery_energy_kwh += charged_energy_kwh
                battery_energy_charged_kwh += charged_energy_kwh
            elif actual_power_kw < target_power_kw:
                requested_discharge_kw = target_power_kw - actual_power_kw
                available_discharge_kw = max(
                    (battery_energy_kwh - min_battery_energy_kwh) / DATA_INTERVAL_HOURS,
                    0,
                )
                battery_discharge_kw = min(
                    requested_discharge_kw,
                    max_discharge_power_kw,
                    available_discharge_kw,
                )
                discharged_energy_kwh = battery_discharge_kw * DATA_INTERVAL_HOURS
                battery_energy_kwh -= discharged_energy_kwh
                battery_energy_used_kwh += discharged_energy_kwh
        else:
            drop_start_power_kw = None
            drop_elapsed_steps = 0

        battery_power_kw = battery_discharge_kw - battery_charge_kw
        raw_with_battery_power_kw = actual_power_kw + battery_power_kw
        curtailed_power_kw = max(raw_with_battery_power_kw - target_power_kw, 0)
        with_battery_power_kw = raw_with_battery_power_kw - curtailed_power_kw
        battery_soc_percent = (
            battery_energy_kwh / capacity_kwh * PERCENT_DIVISOR
            if capacity_kwh
            else 0
        )
        min_battery_soc_percent = min(min_battery_soc_percent, battery_soc_percent)

        target_values.append(target_power_kw)
        with_battery_values.append(with_battery_power_kw)
        battery_power_values.append(battery_power_kw)
        battery_discharge_values.append(battery_discharge_kw)
        battery_charge_values.append(battery_charge_kw)
        curtailed_power_values.append(curtailed_power_kw)
        battery_soc_values.append(battery_soc_percent)

    return MeasurementBatterySimulation(
        target_values=target_values,
        with_battery_values=with_battery_values,
        battery_power_values=battery_power_values,
        battery_discharge_values=battery_discharge_values,
        battery_charge_values=battery_charge_values,
        curtailed_power_values=curtailed_power_values,
        battery_soc_values=battery_soc_values,
        battery_energy_used_kwh=battery_energy_used_kwh,
        battery_energy_charged_kwh=battery_energy_charged_kwh,
        min_battery_soc_percent=min_battery_soc_percent,
    )


async def compare_demo_solar_battery_measurements(
    db: AsyncSession,
    *,
    source_object_name: str = MEASUREMENTS_SOURCE_OBJECT_NAME,
    battery_config: DemoBatteryConfig | None = None,
    cloud_enabled: bool = False,
    cloud_event: DemoCloudEvent = DEMO_CLOUD_EVENT,
    start_time: str = DEFAULT_RANGE_START_TIME,
    end_time: str = DEFAULT_RANGE_END_TIME,
) -> DemoSolarBatteryComparisonResponse:
    battery_config = battery_config or MEASUREMENTS_DEFAULT_BATTERY_CONFIG
    today = datetime.now(ZoneInfo(DEMO_TIMEZONE)).date()
    source_dates = [
        today - timedelta(days=days_back)
        for days_back in MEASUREMENTS_SOURCE_DAYS_BACK
    ]
    minute_labels = _minute_labels()
    demo_objects = _get_measurements_demo_objects(battery_config)

    source_series = [
        await _get_measurement_generation_by_minute(
            db,
            source_date=source_date,
            source_object_name=source_object_name,
        )
        for source_date in source_dates
    ]

    object_actual_values = []
    for series, obj in zip(source_series, demo_objects):
        station_generation_values = [series.get(label, 0.0) for label in minute_labels]
        projected_generation_values = _project_measurement_generation_to_object(
            station_generation_values,
            obj=obj,
        )
        object_actual_values.append(
            _apply_cloud_to_measurement_generation(
                projected_generation_values,
                cloud_enabled=cloud_enabled,
                cloud_event=cloud_event,
            )
        )

    object_battery_simulations = [
        _simulate_measurement_battery(
            actual_values,
            battery_config=battery_config,
            obj=obj,
        )
        for actual_values, obj in zip(object_actual_values, demo_objects)
    ]

    full_grid_timeline: list[DemoGridComparisonPoint] = []
    max_with_battery_drop_kw = 0.0
    max_without_battery_drop_kw = 0.0
    total_battery_capacity_kwh = sum(obj.battery_capacity_kwh for obj in demo_objects)

    for idx, label in enumerate(minute_labels):
        target_power_kw = sum(
            simulation.target_values[idx]
            for simulation in object_battery_simulations
        )
        solar_generation_kw = sum(actual_values[idx] for actual_values in object_actual_values)
        battery_output_kw = sum(
            simulation.battery_discharge_values[idx]
            for simulation in object_battery_simulations
        )
        battery_charge_kw = sum(
            simulation.battery_charge_values[idx]
            for simulation in object_battery_simulations
        )
        battery_power_kw = sum(
            simulation.battery_power_values[idx]
            for simulation in object_battery_simulations
        )
        curtailed_power_kw = sum(
            simulation.curtailed_power_values[idx]
            for simulation in object_battery_simulations
        )
        with_battery_total_output_kw = sum(
            simulation.with_battery_values[idx]
            for simulation in object_battery_simulations
        )
        with_battery_drop_kw = max(target_power_kw - with_battery_total_output_kw, 0)
        without_battery_drop_kw = max(target_power_kw - solar_generation_kw, 0)
        remaining_battery_energy_kwh = sum(
            simulation.battery_soc_values[idx] / PERCENT_DIVISOR * obj.battery_capacity_kwh
            for simulation, obj in zip(object_battery_simulations, demo_objects)
        )
        battery_soc_percent = (
            remaining_battery_energy_kwh / total_battery_capacity_kwh * PERCENT_DIVISOR
            if total_battery_capacity_kwh
            else 0
        )

        max_with_battery_drop_kw = max(max_with_battery_drop_kw, with_battery_drop_kw)
        max_without_battery_drop_kw = max(max_without_battery_drop_kw, without_battery_drop_kw)

        full_grid_timeline.append(
            DemoGridComparisonPoint(
                time=label,
                target_power_kw=round(target_power_kw, OUTPUT_ROUND_DIGITS),
                solar_generation_kw=round(solar_generation_kw, OUTPUT_ROUND_DIGITS),
                with_battery_total_output_kw=round(
                    with_battery_total_output_kw,
                    OUTPUT_ROUND_DIGITS,
                ),
                without_battery_total_output_kw=round(
                    solar_generation_kw,
                    OUTPUT_ROUND_DIGITS,
                ),
                battery_output_kw=round(battery_output_kw, OUTPUT_ROUND_DIGITS),
                battery_charge_kw=round(battery_charge_kw, OUTPUT_ROUND_DIGITS),
                battery_power_kw=round(battery_power_kw, OUTPUT_ROUND_DIGITS),
                curtailed_power_kw=round(curtailed_power_kw, OUTPUT_ROUND_DIGITS),
                with_battery_drop_kw=round(with_battery_drop_kw, OUTPUT_ROUND_DIGITS),
                without_battery_drop_kw=round(without_battery_drop_kw, OUTPUT_ROUND_DIGITS),
                battery_soc_percent=round(battery_soc_percent, OUTPUT_ROUND_DIGITS),
            )
        )

    object_generation_timelines = [
        DemoObjectGenerationTimeline(
            id=obj.id,
            name=obj.name,
            solar_power_kw=obj.solar_power_kw,
            battery_capacity_kwh=obj.battery_capacity_kwh,
            source_object_name=source_object_name,
            source_date=source_date.isoformat(),
            timeline=_filter_timeline_by_range(
                [
                    DemoObjectGenerationPoint(
                        time=label,
                        target_generation_kw=round(
                            battery_simulation.target_values[idx],
                            OUTPUT_ROUND_DIGITS,
                        ),
                        solar_generation_kw=round(
                            actual_values[idx],
                            OUTPUT_ROUND_DIGITS,
                        ),
                        with_battery_total_output_kw=round(
                            battery_simulation.with_battery_values[idx],
                            OUTPUT_ROUND_DIGITS,
                        ),
                        battery_power_kw=round(
                            battery_simulation.battery_power_values[idx],
                            OUTPUT_ROUND_DIGITS,
                        ),
                        battery_discharge_kw=round(
                            battery_simulation.battery_discharge_values[idx],
                            OUTPUT_ROUND_DIGITS,
                        ),
                        battery_charge_kw=round(
                            battery_simulation.battery_charge_values[idx],
                            OUTPUT_ROUND_DIGITS,
                        ),
                        curtailed_power_kw=round(
                            battery_simulation.curtailed_power_values[idx],
                            OUTPUT_ROUND_DIGITS,
                        ),
                        battery_soc_percent=round(
                            battery_simulation.battery_soc_values[idx],
                            OUTPUT_ROUND_DIGITS,
                        ),
                    )
                    for idx, label in enumerate(minute_labels)
                ],
                start_time=start_time,
                end_time=end_time,
            ),
        )
        for obj, source_date, actual_values, battery_simulation in zip(
            demo_objects,
            source_dates,
            object_actual_values,
            object_battery_simulations,
        )
    ]

    installed_power_kw = sum(obj.solar_power_kw for obj in demo_objects)
    battery_capacity_kwh = sum(obj.battery_capacity_kwh for obj in demo_objects)
    battery_energy_used_kwh = sum(
        simulation.battery_energy_used_kwh
        for simulation in object_battery_simulations
    )
    battery_energy_charged_kwh = sum(
        simulation.battery_energy_charged_kwh
        for simulation in object_battery_simulations
    )
    min_battery_soc_percent = min(
        (point.battery_soc_percent for point in full_grid_timeline),
        default=battery_config.initial_soc_percent,
    )

    return DemoSolarBatteryComparisonResponse(
        timezone=DEMO_TIMEZONE,
        data_interval_minutes=DATA_INTERVAL_MINUTES,
        data_source=DemoCompareDataSource.MEASUREMENTS,
        range_start_time=start_time,
        range_end_time=end_time,
        battery_config=battery_config,
        cloud_event=cloud_event if cloud_enabled else None,
        insolation_profile=None,
        cloud_enabled=cloud_enabled,
        objects=[
            DemoEnergyObject(
                id=obj.id,
                name=obj.name,
                solar_power_kw=obj.solar_power_kw,
                battery_capacity_kwh=obj.battery_capacity_kwh,
            )
            for obj in demo_objects
        ],
        object_generation_timelines=object_generation_timelines,
        grid_timeline=_filter_timeline_by_range(
            full_grid_timeline,
            start_time=start_time,
            end_time=end_time,
        ),
        with_battery_summary=DemoSolarBatterySummary(
            installed_power_kw=installed_power_kw,
            battery_capacity_kwh=battery_capacity_kwh,
            max_drop_kw=round(max_with_battery_drop_kw, OUTPUT_ROUND_DIGITS),
            battery_energy_used_kwh=round(battery_energy_used_kwh, OUTPUT_ROUND_DIGITS),
            battery_energy_charged_kwh=round(battery_energy_charged_kwh, OUTPUT_ROUND_DIGITS),
            min_battery_soc_percent=round(min_battery_soc_percent, OUTPUT_ROUND_DIGITS),
        ),
        without_battery_summary=DemoSolarBatterySummary(
            installed_power_kw=installed_power_kw,
            battery_capacity_kwh=battery_capacity_kwh,
            max_drop_kw=round(max_without_battery_drop_kw, OUTPUT_ROUND_DIGITS),
            battery_energy_used_kwh=0,
            battery_energy_charged_kwh=0,
            min_battery_soc_percent=battery_config.initial_soc_percent,
        ),
    )
