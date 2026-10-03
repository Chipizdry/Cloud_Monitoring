from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.database.models import (
    CerboMeasurement,
    DemoSolarBatteryGlobalAlgorithm,
    DemoSolarBatteryObject,
    DemoSolarBatteryObjectAlgorithm,
)
from backend.schemas.energy_demo_v2 import (
    DemoSolarBatteryV2AlgorithmCreate,
    DemoSolarBatteryV2AlgorithmResponse,
    DemoSolarBatteryV2AlgorithmUpdate,
    DemoSolarBatteryV2GlobalAlgorithmCreate,
    DemoSolarBatteryV2GlobalAlgorithmResponse,
    DemoSolarBatteryV2GlobalAlgorithmUpdate,
    DemoSolarBatteryV2GridPoint,
    DemoSolarBatteryV2ObjectCreate,
    DemoSolarBatteryV2ObjectPoint,
    DemoSolarBatteryV2ObjectTimeline,
    DemoSolarBatteryV2ObjectUpdate,
    DemoSolarBatteryV2SimulationResponse,
    DemoSolarBatteryV2SimulationSummary,
)


# Таймзона демо и подписей графиков.
DEMO_V2_TIMEZONE = "Europe/Kyiv"

# Шаг данных для графиков: одна точка на одну минуту.
DEMO_V2_DATA_INTERVAL_MINUTES = 1

# Количество минут в часе для пересчета kW в kWh.
DEMO_V2_MINUTES_PER_HOUR = 60

# Количество часов в сутках.
DEMO_V2_HOURS_PER_DAY = 24

# Количество минут в сутках.
DEMO_V2_DAY_MINUTES = DEMO_V2_HOURS_PER_DAY * DEMO_V2_MINUTES_PER_HOUR

# Делитель для перевода W в kW.
DEMO_V2_WATTS_PER_KILOWATT = 1000

# Делитель для перевода процентов в доли.
DEMO_V2_PERCENT_DIVISOR = 100

# Количество знаков после запятой в ответе API.
DEMO_V2_OUTPUT_ROUND_DIGITS = 3

# Дефолтное начало диапазона графиков.
DEMO_V2_DEFAULT_RANGE_START_TIME = "00:00"

# Дефолтный конец диапазона графиков.
DEMO_V2_DEFAULT_RANGE_END_TIME = "23:59"

# Реальная станция, измерения которой используем как солнечный профиль.
DEMO_V2_SOURCE_OBJECT_NAME = "COR-AZK"

# Пиковая мощность COR-AZK для нормализации реальных измерений в профиль 0..1.
DEMO_V2_SOURCE_PEAK_POWER_KW = 150

# Минимальный день назад, который можно выбрать для солнечного профиля.
DEMO_V2_MIN_DAYS_BACK = 1

# Максимальный день назад, который можно выбрать для солнечного профиля.
DEMO_V2_MAX_DAYS_BACK = 14

# Дефолтный SOC в начале симуляции.
DEMO_V2_INITIAL_SOC_PERCENT = 100

# Минимальная доля пиковой мощности, после которой включаем ограничение роста/падения.
DEMO_V2_RAMP_CONTROL_MIN_POWER_FACTOR = 0.1

# Имя единственной глобальной настройки для второй ступени коррекции.
DEMO_V2_GLOBAL_ALGORITHM_NAME = "default"


class DemoSolarBatteryV2ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SimpleAlgorithmConfig:
    battery_capacity_kwh: float
    support_power_kw: float
    support_peak_minutes: int
    correction_threshold_percent: float
    min_soc_percent: float
    recalculation_period_minutes: int


@dataclass(frozen=True)
class SimpleCorrectionResult:
    target_values: list[float]
    corrected_values: list[float]
    battery_power_values: list[float]
    battery_charge_values: list[float]
    battery_discharge_values: list[float]
    curtailed_power_values: list[float]
    battery_soc_values: list[float]


def _time_to_minutes(value: str) -> int:
    try:
        hours, minutes = value.split(":", 1)
        hours_int = int(hours)
        minutes_int = int(minutes)
    except ValueError as error:
        raise DemoSolarBatteryV2ValidationError(
            "Время должно быть в формате HH:MM."
        ) from error

    if not 0 <= hours_int <= 23 or not 0 <= minutes_int <= 59:
        raise DemoSolarBatteryV2ValidationError(
            "Время должно быть в формате HH:MM от 00:00 до 23:59."
        )

    return hours_int * DEMO_V2_MINUTES_PER_HOUR + minutes_int


def _minutes_to_time(minutes: int) -> str:
    minutes %= DEMO_V2_DAY_MINUTES
    return f"{minutes // DEMO_V2_MINUTES_PER_HOUR:02d}:{minutes % DEMO_V2_MINUTES_PER_HOUR:02d}"


def _minute_labels() -> list[str]:
    return [_minutes_to_time(minute) for minute in range(DEMO_V2_DAY_MINUTES)]


def _is_time_in_range(point_time: str, start_time: str, end_time: str) -> bool:
    point = _time_to_minutes(point_time)
    start = _time_to_minutes(start_time)
    end = _time_to_minutes(end_time)
    if start <= end:
        return start <= point <= end
    return point >= start or point <= end


def _filter_by_time_range(items: list, *, start_time: str, end_time: str) -> list:
    return [
        item
        for item in items
        if _is_time_in_range(item.time, start_time, end_time)
    ]


def _round(value: float) -> float:
    return round(value, DEMO_V2_OUTPUT_ROUND_DIGITS)


def _get_required_battery_capacity_kwh(
    *,
    support_power_kw: float,
    support_peak_minutes: int,
    min_soc_percent: float,
) -> float:
    usable_part = 1 - min_soc_percent / DEMO_V2_PERCENT_DIVISOR
    if usable_part <= 0:
        raise DemoSolarBatteryV2ValidationError(
            "Минимальный SOC слишком высокий: АКБ не сможет отдавать энергию."
        )

    required_usable_kwh = support_power_kw * support_peak_minutes / DEMO_V2_MINUTES_PER_HOUR
    return required_usable_kwh / usable_part


def _validate_battery_capacity(
    *,
    battery_capacity_kwh: float,
    support_power_kw: float,
    support_peak_minutes: int,
    min_soc_percent: float,
) -> None:
    required_capacity_kwh = _get_required_battery_capacity_kwh(
        support_power_kw=support_power_kw,
        support_peak_minutes=support_peak_minutes,
        min_soc_percent=min_soc_percent,
    )
    if battery_capacity_kwh >= required_capacity_kwh:
        return

    raise DemoSolarBatteryV2ValidationError(
        "Такой алгоритм невозможен для этой АКБ. "
        f"Нужно минимум {_round(required_capacity_kwh)} kWh, "
        f"сейчас указано {_round(battery_capacity_kwh)} kWh."
    )


def _validate_days_back(days_back: int) -> None:
    if DEMO_V2_MIN_DAYS_BACK <= days_back <= DEMO_V2_MAX_DAYS_BACK:
        return
    raise DemoSolarBatteryV2ValidationError(
        f"days_back должен быть от {DEMO_V2_MIN_DAYS_BACK} до {DEMO_V2_MAX_DAYS_BACK}."
    )


def _get_default_days_back(index: int) -> int:
    return index % DEMO_V2_MAX_DAYS_BACK + DEMO_V2_MIN_DAYS_BACK


def _get_source_days_back(
    *,
    objects_count: int,
    days_back: int | None,
) -> list[int]:
    if days_back is not None:
        _validate_days_back(days_back)
        return [days_back for _ in range(objects_count)]

    return [_get_default_days_back(index) for index in range(objects_count)]


async def get_demo_solar_battery_v2_objects(
    db: AsyncSession,
) -> list[DemoSolarBatteryObject]:
    result = await db.execute(
        select(DemoSolarBatteryObject)
        .options(selectinload(DemoSolarBatteryObject.correction_algorithm))
        .order_by(DemoSolarBatteryObject.created_at.asc())
    )
    return list(result.scalars().all())


async def get_demo_solar_battery_v2_object(
    db: AsyncSession,
    object_id: str,
) -> DemoSolarBatteryObject | None:
    result = await db.execute(
        select(DemoSolarBatteryObject)
        .options(selectinload(DemoSolarBatteryObject.correction_algorithm))
        .where(DemoSolarBatteryObject.id == object_id)
    )
    return result.scalar_one_or_none()


async def create_demo_solar_battery_v2_object(
    db: AsyncSession,
    payload: DemoSolarBatteryV2ObjectCreate,
) -> DemoSolarBatteryObject:
    obj = DemoSolarBatteryObject(**payload.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def update_demo_solar_battery_v2_object(
    db: AsyncSession,
    object_id: str,
    payload: DemoSolarBatteryV2ObjectUpdate,
) -> DemoSolarBatteryObject | None:
    obj = await get_demo_solar_battery_v2_object(db, object_id)
    if obj is None:
        return None

    update_data = payload.model_dump(exclude_unset=True)
    if obj.correction_algorithm is not None:
        peak_power_kw = update_data.get("peak_power_kw", obj.peak_power_kw)
        battery_capacity_kwh = update_data.get(
            "battery_capacity_kwh",
            obj.battery_capacity_kwh,
        )
        _validate_battery_capacity(
            battery_capacity_kwh=battery_capacity_kwh,
            support_power_kw=peak_power_kw,
            support_peak_minutes=obj.correction_algorithm.support_peak_minutes,
            min_soc_percent=obj.correction_algorithm.min_soc_percent,
        )

    for field_name, field_value in update_data.items():
        setattr(obj, field_name, field_value)

    await db.commit()
    await db.refresh(obj)
    return await get_demo_solar_battery_v2_object(db, object_id)


async def delete_demo_solar_battery_v2_object(
    db: AsyncSession,
    object_id: str,
) -> bool:
    obj = await get_demo_solar_battery_v2_object(db, object_id)
    if obj is None:
        return False
    await db.delete(obj)
    await db.commit()
    return True


async def upsert_demo_solar_battery_v2_object_algorithm(
    db: AsyncSession,
    object_id: str,
    payload: DemoSolarBatteryV2AlgorithmCreate,
) -> DemoSolarBatteryObjectAlgorithm | None:
    obj = await get_demo_solar_battery_v2_object(db, object_id)
    if obj is None:
        return None

    _validate_battery_capacity(
        battery_capacity_kwh=obj.battery_capacity_kwh,
        support_power_kw=obj.peak_power_kw,
        support_peak_minutes=payload.support_peak_minutes,
        min_soc_percent=payload.min_soc_percent,
    )

    if obj.correction_algorithm is None:
        algorithm = DemoSolarBatteryObjectAlgorithm(
            object_id=obj.id,
            **payload.model_dump(),
        )
        db.add(algorithm)
    else:
        algorithm = obj.correction_algorithm
        for field_name, field_value in payload.model_dump().items():
            setattr(algorithm, field_name, field_value)

    await db.commit()
    await db.refresh(algorithm)
    return algorithm


async def update_demo_solar_battery_v2_object_algorithm(
    db: AsyncSession,
    object_id: str,
    payload: DemoSolarBatteryV2AlgorithmUpdate,
) -> DemoSolarBatteryObjectAlgorithm | None:
    obj = await get_demo_solar_battery_v2_object(db, object_id)
    if obj is None or obj.correction_algorithm is None:
        return None

    algorithm = obj.correction_algorithm
    update_data = payload.model_dump(exclude_unset=True)
    support_peak_minutes = update_data.get(
        "support_peak_minutes",
        algorithm.support_peak_minutes,
    )
    min_soc_percent = update_data.get("min_soc_percent", algorithm.min_soc_percent)

    _validate_battery_capacity(
        battery_capacity_kwh=obj.battery_capacity_kwh,
        support_power_kw=obj.peak_power_kw,
        support_peak_minutes=support_peak_minutes,
        min_soc_percent=min_soc_percent,
    )

    for field_name, field_value in update_data.items():
        setattr(algorithm, field_name, field_value)

    await db.commit()
    await db.refresh(algorithm)
    return algorithm


async def delete_demo_solar_battery_v2_object_algorithm(
    db: AsyncSession,
    object_id: str,
) -> bool:
    obj = await get_demo_solar_battery_v2_object(db, object_id)
    if obj is None or obj.correction_algorithm is None:
        return False
    await db.delete(obj.correction_algorithm)
    await db.commit()
    return True


async def get_demo_solar_battery_v2_global_algorithm(
    db: AsyncSession,
) -> DemoSolarBatteryGlobalAlgorithm | None:
    result = await db.execute(
        select(DemoSolarBatteryGlobalAlgorithm).where(
            DemoSolarBatteryGlobalAlgorithm.name == DEMO_V2_GLOBAL_ALGORITHM_NAME
        )
    )
    return result.scalar_one_or_none()


async def upsert_demo_solar_battery_v2_global_algorithm(
    db: AsyncSession,
    payload: DemoSolarBatteryV2GlobalAlgorithmCreate,
    *,
    default_support_power_kw: float,
) -> DemoSolarBatteryGlobalAlgorithm:
    support_power_kw = payload.support_power_kw or default_support_power_kw
    _validate_battery_capacity(
        battery_capacity_kwh=payload.external_battery_capacity_kwh,
        support_power_kw=support_power_kw,
        support_peak_minutes=payload.support_peak_minutes,
        min_soc_percent=payload.min_soc_percent,
    )

    algorithm = await get_demo_solar_battery_v2_global_algorithm(db)
    if algorithm is None:
        algorithm = DemoSolarBatteryGlobalAlgorithm(
            name=DEMO_V2_GLOBAL_ALGORITHM_NAME,
            **payload.model_dump(),
        )
        db.add(algorithm)
    else:
        for field_name, field_value in payload.model_dump().items():
            setattr(algorithm, field_name, field_value)

    await db.commit()
    await db.refresh(algorithm)
    return algorithm


async def update_demo_solar_battery_v2_global_algorithm(
    db: AsyncSession,
    payload: DemoSolarBatteryV2GlobalAlgorithmUpdate,
    *,
    default_support_power_kw: float,
) -> DemoSolarBatteryGlobalAlgorithm | None:
    algorithm = await get_demo_solar_battery_v2_global_algorithm(db)
    if algorithm is None:
        return None

    update_data = payload.model_dump(exclude_unset=True)
    external_battery_capacity_kwh = update_data.get(
        "external_battery_capacity_kwh",
        algorithm.external_battery_capacity_kwh,
    )
    support_power_kw = (
        update_data.get("support_power_kw")
        or algorithm.support_power_kw
        or default_support_power_kw
    )
    support_peak_minutes = update_data.get(
        "support_peak_minutes",
        algorithm.support_peak_minutes,
    )
    min_soc_percent = update_data.get("min_soc_percent", algorithm.min_soc_percent)

    _validate_battery_capacity(
        battery_capacity_kwh=external_battery_capacity_kwh,
        support_power_kw=support_power_kw,
        support_peak_minutes=support_peak_minutes,
        min_soc_percent=min_soc_percent,
    )

    for field_name, field_value in update_data.items():
        setattr(algorithm, field_name, field_value)

    await db.commit()
    await db.refresh(algorithm)
    return algorithm


async def delete_demo_solar_battery_v2_global_algorithm(db: AsyncSession) -> bool:
    algorithm = await get_demo_solar_battery_v2_global_algorithm(db)
    if algorithm is None:
        return False
    await db.delete(algorithm)
    await db.commit()
    return True


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
            _round(float(solar_power_w) / DEMO_V2_WATTS_PER_KILOWATT),
            0,
        )
    return values


def _project_station_profile_to_object(
    station_values: list[float],
    *,
    peak_power_kw: float,
) -> list[float]:
    result: list[float] = []
    for station_power_kw in station_values:
        profile_part = station_power_kw / DEMO_V2_SOURCE_PEAK_POWER_KW
        profile_part = max(min(profile_part, 1), 0)
        result.append(_round(peak_power_kw * profile_part))
    return result


def _simulate_simple_correction(
    values_without_correction: list[float],
    *,
    config: SimpleAlgorithmConfig,
) -> SimpleCorrectionResult:
    battery_energy_kwh = (
        config.battery_capacity_kwh
        * DEMO_V2_INITIAL_SOC_PERCENT
        / DEMO_V2_PERCENT_DIVISOR
    )
    min_battery_energy_kwh = (
        config.battery_capacity_kwh
        * config.min_soc_percent
        / DEMO_V2_PERCENT_DIVISOR
    )
    threshold = config.correction_threshold_percent / DEMO_V2_PERCENT_DIVISOR
    interval_hours = DEMO_V2_DATA_INTERVAL_MINUTES / DEMO_V2_MINUTES_PER_HOUR
    ramp_control_min_power_kw = (
        config.support_power_kw * DEMO_V2_RAMP_CONTROL_MIN_POWER_FACTOR
    )

    targets: list[float] = []
    corrected: list[float] = []
    battery_power: list[float] = []
    battery_charge: list[float] = []
    battery_discharge: list[float] = []
    curtailed: list[float] = []
    soc: list[float] = []

    previous_target_kw = values_without_correction[0] if values_without_correction else 0
    generation_started = False

    for start in range(0, len(values_without_correction), config.recalculation_period_minutes):
        period_values = values_without_correction[
            start : start + config.recalculation_period_minutes
        ]
        if not period_values:
            continue

        # Раз в N минут смотрим среднюю солнечную отдачу за новый период.
        sun_average_kw = sum(period_values) / len(period_values)

        # Утром и вечером, пока мощность совсем маленькая, не душим график процентным
        # лимитом от почти нуля. Иначе target растет как 0.001 -> 0.0011 -> 0.00121.
        if (
            previous_target_kw < ramp_control_min_power_kw
            or sun_average_kw < ramp_control_min_power_kw
        ):
            target_kw = sun_average_kw
        else:
            # Новый план не должен прыгать сильнее заданного процента.
            min_target_kw = previous_target_kw * (1 - threshold)
            max_target_kw = previous_target_kw * (1 + threshold)
            if sun_average_kw > max_target_kw:
                target_kw = max_target_kw
            elif sun_average_kw < min_target_kw:
                target_kw = min_target_kw
            else:
                target_kw = sun_average_kw
        previous_target_kw = target_kw

        for solar_kw in period_values:
            if solar_kw > 0:
                generation_started = True
            # Среднее за период может учитывать будущие солнечные минуты.
            # До первого появления генерации АКБ не выдаёт энергию в сеть;
            # после запуска она поддерживает отдачу и при нулевой генерации.
            effective_target_kw = target_kw if generation_started else 0.0
            charge_kw = 0.0
            discharge_kw = 0.0
            cut_kw = 0.0

            if solar_kw > effective_target_kw:
                # Солнца слишком много: в сеть отдаем план, остальное заряжаем или режем.
                extra_kw = solar_kw - effective_target_kw
                possible_charge_kw = max(
                    (config.battery_capacity_kwh - battery_energy_kwh) / interval_hours,
                    0,
                )
                charge_kw = min(extra_kw, config.support_power_kw, possible_charge_kw)
                cut_kw = extra_kw - charge_kw
                battery_energy_kwh += charge_kw * interval_hours
                output_kw = effective_target_kw
            elif solar_kw < effective_target_kw:
                # Солнца не хватает: пытаемся добрать недостающее из АКБ.
                missing_kw = effective_target_kw - solar_kw
                possible_discharge_kw = max(
                    (battery_energy_kwh - min_battery_energy_kwh) / interval_hours,
                    0,
                )
                discharge_kw = min(
                    missing_kw,
                    config.support_power_kw,
                    possible_discharge_kw,
                )
                battery_energy_kwh -= discharge_kw * interval_hours
                output_kw = solar_kw + discharge_kw
            else:
                output_kw = solar_kw

            targets.append(_round(effective_target_kw))
            corrected.append(_round(output_kw))
            battery_charge.append(_round(charge_kw))
            battery_discharge.append(_round(discharge_kw))
            battery_power.append(_round(discharge_kw - charge_kw))
            curtailed.append(_round(cut_kw))
            soc.append(
                _round(
                    battery_energy_kwh
                    / config.battery_capacity_kwh
                    * DEMO_V2_PERCENT_DIVISOR
                )
            )

    return SimpleCorrectionResult(
        target_values=targets,
        corrected_values=corrected,
        battery_power_values=battery_power,
        battery_charge_values=battery_charge,
        battery_discharge_values=battery_discharge,
        curtailed_power_values=curtailed,
        battery_soc_values=soc,
    )


def _object_algorithm_to_config(
    obj: DemoSolarBatteryObject,
    algorithm: DemoSolarBatteryObjectAlgorithm,
) -> SimpleAlgorithmConfig:
    return SimpleAlgorithmConfig(
        battery_capacity_kwh=obj.battery_capacity_kwh,
        support_power_kw=obj.peak_power_kw,
        support_peak_minutes=algorithm.support_peak_minutes,
        correction_threshold_percent=algorithm.correction_threshold_percent,
        min_soc_percent=algorithm.min_soc_percent,
        recalculation_period_minutes=algorithm.recalculation_period_minutes,
    )


def _global_algorithm_to_config(
    algorithm: DemoSolarBatteryGlobalAlgorithm,
    *,
    default_support_power_kw: float,
) -> SimpleAlgorithmConfig:
    return SimpleAlgorithmConfig(
        battery_capacity_kwh=algorithm.external_battery_capacity_kwh,
        support_power_kw=algorithm.support_power_kw or default_support_power_kw,
        support_peak_minutes=algorithm.support_peak_minutes,
        correction_threshold_percent=algorithm.correction_threshold_percent,
        min_soc_percent=algorithm.min_soc_percent,
        recalculation_period_minutes=algorithm.recalculation_period_minutes,
    )


def _empty_correction(values: list[float]) -> SimpleCorrectionResult:
    soc = [DEMO_V2_INITIAL_SOC_PERCENT for _ in values]
    zeros = [0.0 for _ in values]
    return SimpleCorrectionResult(
        target_values=list(values),
        corrected_values=list(values),
        battery_power_values=zeros,
        battery_charge_values=zeros,
        battery_discharge_values=zeros,
        curtailed_power_values=zeros,
        battery_soc_values=soc,
    )


async def simulate_demo_solar_battery_v2(
    db: AsyncSession,
    *,
    source_object_name: str = DEMO_V2_SOURCE_OBJECT_NAME,
    days_back: int | None = None,
    start_time: str = DEMO_V2_DEFAULT_RANGE_START_TIME,
    end_time: str = DEMO_V2_DEFAULT_RANGE_END_TIME,
) -> DemoSolarBatteryV2SimulationResponse:
    objects = await get_demo_solar_battery_v2_objects(db)
    selected_days_back = _get_source_days_back(
        objects_count=len(objects),
        days_back=days_back,
    )
    today = datetime.now(ZoneInfo(DEMO_V2_TIMEZONE)).date()
    minute_labels = _minute_labels()

    object_timelines: list[DemoSolarBatteryV2ObjectTimeline] = []
    object_raw_values: list[list[float]] = []
    object_corrected_values: list[list[float]] = []

    for obj, days_back_value in zip(objects, selected_days_back):
        source_date = today - timedelta(days=days_back_value)
        station_series = await _get_measurement_generation_by_minute(
            db,
            source_date=source_date,
            source_object_name=source_object_name,
        )
        station_values = [station_series.get(label, 0.0) for label in minute_labels]
        raw_values = _project_station_profile_to_object(
            station_values,
            peak_power_kw=obj.peak_power_kw,
        )

        if obj.correction_algorithm is None:
            correction = _empty_correction(
                raw_values,
            )
            algorithm_response = None
        else:
            correction = _simulate_simple_correction(
                raw_values,
                config=_object_algorithm_to_config(obj, obj.correction_algorithm),
            )
            algorithm_response = DemoSolarBatteryV2AlgorithmResponse.model_validate(
                obj.correction_algorithm
            )

        object_raw_values.append(raw_values)
        object_corrected_values.append(correction.corrected_values)

        full_timeline = [
            DemoSolarBatteryV2ObjectPoint(
                time=label,
                without_correction_kw=raw_values[index],
                with_correction_kw=correction.corrected_values[index],
                target_output_kw=correction.target_values[index],
                battery_power_kw=correction.battery_power_values[index],
                battery_charge_kw=correction.battery_charge_values[index],
                battery_discharge_kw=correction.battery_discharge_values[index],
                curtailed_power_kw=correction.curtailed_power_values[index],
                battery_soc_percent=correction.battery_soc_values[index],
            )
            for index, label in enumerate(minute_labels)
        ]

        object_timelines.append(
            DemoSolarBatteryV2ObjectTimeline(
                id=obj.id,
                name=obj.name,
                peak_power_kw=obj.peak_power_kw,
                battery_capacity_kwh=obj.battery_capacity_kwh,
                source_object_name=source_object_name,
                source_date=source_date.isoformat(),
                algorithm=algorithm_response,
                timeline=_filter_by_time_range(
                    full_timeline,
                    start_time=start_time,
                    end_time=end_time,
                ),
            )
        )

    grid_without_correction = [
        _round(sum(values[index] for values in object_raw_values))
        for index in range(len(minute_labels))
    ]
    grid_after_object_correction = [
        _round(sum(values[index] for values in object_corrected_values))
        for index in range(len(minute_labels))
    ]

    global_algorithm = await get_demo_solar_battery_v2_global_algorithm(db)
    installed_power_kw = sum(obj.peak_power_kw for obj in objects)
    if global_algorithm is None:
        global_correction = _empty_correction(
            grid_after_object_correction,
        )
        global_algorithm_response = None
    else:
        global_correction = _simulate_simple_correction(
            grid_after_object_correction,
            config=_global_algorithm_to_config(
                global_algorithm,
                default_support_power_kw=installed_power_kw,
            ),
        )
        global_algorithm_response = DemoSolarBatteryV2GlobalAlgorithmResponse.model_validate(
            global_algorithm
        )

    full_grid_timeline = [
        DemoSolarBatteryV2GridPoint(
            time=label,
            without_correction_kw=grid_without_correction[index],
            after_object_correction_kw=grid_after_object_correction[index],
            with_global_correction_kw=global_correction.corrected_values[index],
            global_target_output_kw=global_correction.target_values[index],
            global_battery_power_kw=global_correction.battery_power_values[index],
            global_battery_charge_kw=global_correction.battery_charge_values[index],
            global_battery_discharge_kw=global_correction.battery_discharge_values[index],
            global_curtailed_power_kw=global_correction.curtailed_power_values[index],
            global_battery_soc_percent=global_correction.battery_soc_values[index],
        )
        for index, label in enumerate(minute_labels)
    ]

    filtered_grid_timeline = _filter_by_time_range(
        full_grid_timeline,
        start_time=start_time,
        end_time=end_time,
    )

    return DemoSolarBatteryV2SimulationResponse(
        timezone=DEMO_V2_TIMEZONE,
        data_interval_minutes=DEMO_V2_DATA_INTERVAL_MINUTES,
        source_object_name=source_object_name,
        range_start_time=start_time,
        range_end_time=end_time,
        object_timelines=object_timelines,
        grid_timeline=filtered_grid_timeline,
        global_algorithm=global_algorithm_response,
        summary=DemoSolarBatteryV2SimulationSummary(
            objects_count=len(objects),
            installed_power_kw=installed_power_kw,
            objects_battery_capacity_kwh=sum(obj.battery_capacity_kwh for obj in objects),
            external_battery_capacity_kwh=(
                global_algorithm.external_battery_capacity_kwh
                if global_algorithm is not None
                else None
            ),
            data_points_per_timeline=len(filtered_grid_timeline),
        ),
    )
