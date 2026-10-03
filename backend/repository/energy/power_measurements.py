from datetime import datetime, timedelta
from typing import Any, Iterable, Optional, Tuple

from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import CerboMeasurement, EnergeticDevice, EnergeticObject, PowerMeasurement
from backend.schemas.device_measurement import (
    CerboMeasurementResponse,
    PaginatedResponse,
    PowerMeasurementCreate,
    PowerMeasurementResponse,
)


DEYE_COR_BRIDGE_HISTORY_EVENT_MAX_AGE_SECONDS = 15


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(data: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _as_float(data.get(key))
        if value is not None:
            return value
    return None


def _sum_numbers(data: dict[str, Any], *keys: str) -> float | None:
    values = [_as_float(data.get(key)) for key in keys]
    values = [value for value in values if value is not None]
    return sum(values) if values else None


def _first_non_zero_number(data: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _as_float(data.get(key))
        if value is not None and value != 0:
            return value
    return None


def _is_zero(value: float | None) -> bool:
    return value is not None and value == 0


def _signed_32_from_words(low_word: Any, high_word: Any) -> float | None:
    low = _as_float(low_word)
    high = _as_float(high_word)
    if low is None or high is None:
        return None
    combined = (int(high) << 16) | (int(low) & 0xFFFF)
    if combined > 0x7FFFFFFF:
        combined -= 0x100000000
    return float(combined)


def _calculate_deye_voltage_soc(snapshot: dict[str, Any]) -> float | None:
    battery_work_mode = (
        snapshot.get("battery_work_mode")
        if snapshot.get("battery_work_mode") is not None
        else snapshot.get("batteryWorkMode")
    )
    if battery_work_mode not in (0, 0.0, "0", "voltage"):
        return None

    voltages = [
        value
        for value in (
            _as_float(
                snapshot.get("battery1_voltage")
                if snapshot.get("battery1_voltage") is not None
                else snapshot.get("battery1Voltage")
            ),
            _as_float(
                snapshot.get("battery2_voltage")
                if snapshot.get("battery2_voltage") is not None
                else snapshot.get("battery2Voltage")
            ),
        )
        if value is not None and value > 0
    ]
    if not voltages:
        return None

    voltage_max = _as_float(
        snapshot.get("battery_float_voltage")
        if snapshot.get("battery_float_voltage") is not None
        else snapshot.get("batteryFloatVoltage")
    )
    voltage_min = _as_float(
        snapshot.get("battery_voltage_shutdown")
        if snapshot.get("battery_voltage_shutdown") is not None
        else snapshot.get("batteryVoltageShutdown")
    )
    if voltage_max is None or voltage_min is None or voltage_max <= voltage_min:
        return None

    voltage_avg = sum(voltages) / len(voltages)
    soc = ((voltage_avg - voltage_min) / (voltage_max - voltage_min)) * 100
    return round(max(0.0, min(100.0, soc)), 1)


def _battery_soc_from_snapshot(snapshot: dict[str, Any]) -> float | None:
    first_battery_soc = _first_number(snapshot, "battery1_soc", "battery1SOC")
    if first_battery_soc is not None and first_battery_soc != 0:
        return first_battery_soc

    primary_soc = _first_number(snapshot, "battery_soc", "soc", "batterySOC", "calculatedSOC", "calculated_soc")
    if primary_soc is not None and primary_soc != 0:
        return primary_soc

    voltage_soc = _calculate_deye_voltage_soc(snapshot)
    if voltage_soc is not None:
        return voltage_soc

    second_battery_soc = _first_non_zero_number(snapshot, "battery2_soc", "battery2SOC")
    if second_battery_soc is not None:
        return second_battery_soc

    if first_battery_soc is not None:
        return first_battery_soc

    if primary_soc is not None:
        return primary_soc
    return _first_number(snapshot, "battery2_soc", "battery2SOC")


def _battery_voltage_from_snapshot(snapshot: dict[str, Any]) -> float | None:
    return _first_number(
        snapshot,
        "battery1_voltage",
        "battery1Voltage",
        "battery_voltage_v",
        "battery_voltage",
        "batteryVoltage",
        "battery2_voltage",
        "battery2Voltage",
    )


def _load_power_from_snapshot(snapshot: dict[str, Any]) -> float | None:
    load_total_power = _signed_32_from_words(
        snapshot.get("load_total_power_low"),
        snapshot.get("load_total_power_high"),
    )
    if load_total_power is not None:
        return load_total_power

    return _first_number(
        snapshot,
        "load_power_w",
        "LoadTotalPower",
        "load_total_power_high",
        "load_total_power",
        "inverter_total_power_high",
        "inverter_total_power",
        "InverterTotalPower_high",
        "InverterTotalPower",
        "out_total_power",
        "outTotalPower",
        "outputActivePower",
        "outputApparentPower",
        "inverter_total_ac_output",
    )


def _deye_cor_bridge_power_values(snapshot: dict[str, Any]) -> dict[str, float | None]:
    battery_power = _first_number(snapshot, "batteryTotalPower")
    if battery_power is None:
        battery_power = _sum_numbers(snapshot, "battery1Power", "battery2Power")

    solar_power = _first_number(snapshot, "TotalPVPower", "PVTotalPower")
    if solar_power is None:
        pv_low = _as_float(snapshot.get("PVTotalPower_low"))
        pv_high = _as_float(snapshot.get("PVTotalPower_high"))
        if pv_low is not None or pv_high is not None:
            solar_power = (pv_low or 0) + (pv_high or 0)
    if solar_power is None:
        solar_power = _sum_numbers(
            snapshot,
            "PV1Power",
            "PV2Power",
            "PV3Power",
            "PV4Power",
            "PV5Power",
            "PV6Power",
            "PV7Power",
            "PV8Power",
        )

    return {
        "battery_power": battery_power,
        "battery_voltage": _battery_voltage_from_snapshot(snapshot),
        "battery_soc": _battery_soc_from_snapshot(snapshot),
        "solar_power": solar_power,
        "load_power": _first_number(snapshot, "LoadTotalPower"),
        "grid_power": _first_number(snapshot, "inputPowerTotal"),
        "generator_power": _first_number(snapshot, "GenTotalPower"),
    }


def build_power_measurement_from_snapshot(
    *,
    object_id: str,
    object_name: str,
    snapshot: dict[str, Any],
    source_protocol: str | None = None,
    source_task_id: str | None = None,
) -> PowerMeasurementCreate | None:
    measured_at_raw = snapshot.get("measured_at")
    if isinstance(measured_at_raw, datetime):
        measured_at = measured_at_raw
    elif isinstance(measured_at_raw, str):
        try:
            measured_at = datetime.fromisoformat(measured_at_raw)
        except ValueError:
            measured_at = datetime.now()
    else:
        measured_at = datetime.now()

    if source_protocol == "cor_bridge_modbus_ws":
        power_values = _deye_cor_bridge_power_values(snapshot)
        battery_power = power_values["battery_power"]
        battery_voltage = power_values["battery_voltage"]
        battery_soc = power_values["battery_soc"]
        solar_power = power_values["solar_power"]
        load_power = power_values["load_power"]
        grid_power = power_values["grid_power"]
        generator_power = power_values["generator_power"]
    else:
        battery_power = _first_number(
            snapshot,
            "battery_power_w",
            "general_battery_power",
            "batteryTotalPower",
            "battery_total_power",
        )
        if battery_power is None:
            battery_power = _sum_numbers(snapshot, "battery1_power", "battery2_power")

        battery_voltage = _battery_voltage_from_snapshot(snapshot)
        battery_soc = _battery_soc_from_snapshot(snapshot)

        solar_power = _first_number(
            snapshot,
            "solar_power_w",
            "solar_total_pv_power",
            "pv_total_power",
            "TotalPVPower",
            "PVTotalPower",
            "pv_total_power_raw_high",
            "PVTotalPower_high",
            "PVTotalPower_low",
            "solarPower",
            "pvInputPower",
        )
        if solar_power is None:
            solar_power = _sum_numbers(
                snapshot,
                "pv1_power",
                "pv2_power",
                "pv3_power",
                "pv4_power",
                "PV1Power",
                "PV2Power",
                "PV3Power",
                "PV4Power",
                "PV5Power",
                "PV6Power",
                "PV7Power",
                "PV8Power",
            )

        load_power = _load_power_from_snapshot(snapshot)

        grid_power = _first_number(
            snapshot,
            "grid_power_w",
            "ess_total_input_power",
            "grid_total_power",
            "total_power",
            "inputPowerTotal",
            "GridTotalPower_high",
            "GridTotalPower",
            "grid_total_power_high",
            "inputPower",
        )

        generator_power = _first_number(
            snapshot,
            "generator_power_w",
            "gen_total_power",
            "gen_total_power_high",
            "gen_total_power_low",
        )

    if all(
        value is None
        for value in (
            solar_power,
            battery_power,
            battery_voltage,
            battery_soc,
            grid_power,
            load_power,
            generator_power,
        )
    ):
        return None

    return PowerMeasurementCreate(
        measured_at=measured_at,
        energetic_object_id=object_id,
        object_name=object_name,
        source_protocol=source_protocol,
        source_task_id=source_task_id,
        solar_power_w=solar_power,
        battery_power_w=battery_power,
        battery_voltage_v=battery_voltage,
        battery_soc=battery_soc,
        grid_power_w=grid_power,
        load_power_w=load_power,
        generator_power_w=generator_power,
        raw_snapshot={k: v for k, v in snapshot.items() if isinstance(v, (str, int, float, bool, type(None)))},
    )


async def create_power_measurement(
    db: AsyncSession,
    data: PowerMeasurementCreate,
) -> PowerMeasurementResponse:
    db_measurement = PowerMeasurement(**data.model_dump())
    db.add(db_measurement)
    await db.commit()
    await db.refresh(db_measurement)
    return PowerMeasurementResponse.model_validate(db_measurement)


async def persist_power_measurement_snapshot(
    *,
    object_id: str,
    object_name: str,
    snapshot: dict[str, Any],
    source_protocol: str | None = None,
    source_task_id: str | None = None,
) -> None:
    measurement = build_power_measurement_from_snapshot(
        object_id=object_id,
        object_name=object_name,
        snapshot=snapshot,
        source_protocol=source_protocol,
        source_task_id=source_task_id,
    )
    if measurement is None:
        return

    from backend.database.db import async_session_maker

    try:
        async with async_session_maker() as db:
            await create_power_measurement(db, measurement)
    except Exception as e:
        logger.error(
            f"Failed to persist power measurement for {object_name} ({object_id}): {e}",
            exc_info=True,
        )


async def persist_pi30_power_measurement_from_ws(
    *,
    device_aliases: Iterable[str],
    raw_event: dict[str, Any],
    measured_at: Optional[datetime] = None,
) -> bool:
    from backend.database.db import async_session_maker
    from backend.services.energy.pi30_parser import parse_pi30_event

    try:
        parsed_pi30 = parse_pi30_event(raw_event)
    except Exception as exc:
        logger.warning(f"Failed to parse PI30 event for history persistence: {exc}")
        return False

    if not parsed_pi30 or parsed_pi30.get("status") != "ok" or not isinstance(parsed_pi30.get("parsed"), dict):
        return False

    aliases = {str(alias).strip() for alias in device_aliases if alias and str(alias).strip()}
    if not aliases:
        return False

    try:
        async with async_session_maker() as db:
            resolved_object = await _resolve_energetic_object_by_bridge_aliases(db, aliases)
            if resolved_object is None:
                return False

            object_id, object_name = resolved_object
            snapshot = dict(parsed_pi30["parsed"])
            snapshot["measured_at"] = measured_at or datetime.utcnow()
            snapshot["pi30_command"] = parsed_pi30.get("cmd")
            snapshot["pi30_status"] = parsed_pi30.get("status")

            measurement = build_power_measurement_from_snapshot(
                object_id=object_id,
                object_name=object_name,
                snapshot=snapshot,
                source_protocol="pi30_ws",
                source_task_id=parsed_pi30.get("cmd"),
            )
            if measurement is None:
                return False

            await create_power_measurement(db, measurement)
            return True
    except Exception as exc:
        logger.error(f"Failed to persist PI30 WS power measurement for aliases {sorted(aliases)}: {exc}", exc_info=True)
        return False


def _hex_response_to_registers(hex_response: Any) -> list[int] | None:
    if not isinstance(hex_response, str):
        return None

    clean = "".join(hex_response.split())
    if len(clean) < 10 or len(clean) % 2 != 0:
        return None

    try:
        payload = bytes.fromhex(clean)
    except ValueError:
        return None

    if len(payload) < 5:
        return None

    byte_count = payload[2]
    data = payload[3: 3 + byte_count]
    if len(data) < byte_count or len(data) % 2 != 0:
        return None

    return [
        (data[index] << 8) | data[index + 1]
        for index in range(0, len(data), 2)
    ]


def _signed_16(value: int) -> int:
    return value - 0x10000 if value > 0x7FFF else value


def _signed_32_from_registers(low_word: int, high_word: int) -> int:
    combined = (int(high_word) << 16) | (int(low_word) & 0xFFFF)
    return combined - 0x100000000 if combined > 0x7FFFFFFF else combined


def _event_timestamp(raw_event: dict[str, Any]) -> datetime | None:
    timestamp = raw_event.get("timestamp")
    if isinstance(timestamp, datetime):
        return timestamp
    if isinstance(timestamp, str):
        try:
            return datetime.fromisoformat(timestamp)
        except ValueError:
            return None
    return None


def _is_event_fresh_for_history(
    raw_event: dict[str, Any],
    measured_at: datetime,
    *,
    max_age_seconds: int = DEYE_COR_BRIDGE_HISTORY_EVENT_MAX_AGE_SECONDS,
) -> bool:
    event_time = _event_timestamp(raw_event)
    if event_time is None:
        return True
    return abs((measured_at - event_time).total_seconds()) <= max_age_seconds


def _parse_deye_modbus_read_event(raw_event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    nested = raw_event.get("data") if isinstance(raw_event.get("data"), dict) else {}
    command_name = (
        nested.get("command_name")
        or raw_event.get("command_name")
        or nested.get("cmd")
        or raw_event.get("cmd")
    )
    command_name = str(command_name or "").strip()
    if not command_name:
        return None

    hex_response = (
        nested.get("hex_response")
        or raw_event.get("hex_response")
        or nested.get("hex_data")
        or raw_event.get("hex_data")
    )
    registers = _hex_response_to_registers(hex_response)
    if not registers:
        return None

    def reg(index: int, default: int = 0) -> int:
        return registers[index] if 0 <= index < len(registers) else default

    parsed: dict[str, Any] = {}

    if command_name == "batt":
        fields = [
            ("battery1Temperature", 0.1, False),
            ("battery1Voltage", 0.1, False),
            ("battery1SOC", 1, False),
            ("battery2SOC", 1, False),
            ("battery1Power", 10, True),
            ("battery1Current", 0.01, True),
            ("batteryCorrectedAh", 1, False),
            ("battery2Voltage", 0.1, False),
            ("battery2Current", 0.01, True),
            ("battery2Power", 10, True),
            ("battery2Temperature", 0.1, False),
        ]
        for index, (name, scale, signed) in enumerate(fields):
            value = reg(index)
            parsed[name] = (_signed_16(value) if signed else value) * scale
        parsed["batteryTotalPower"] = parsed.get("battery1Power", 0) + parsed.get("battery2Power", 0)

    elif command_name == "PV1-4":
        fields = [
            ("PV1Power", 10),
            ("PV2Power", 10),
            ("PV3Power", 10),
            ("PV4Power", 10),
            ("PV1Voltage", 0.1),
            ("PV1Current", 0.1),
            ("PV2Voltage", 0.1),
            ("PV2Current", 0.1),
            ("PV3Voltage", 0.1),
            ("PV3Current", 0.1),
            ("PV4Voltage", 0.1),
            ("PV4Current", 0.1),
        ]
        for index, (name, scale) in enumerate(fields):
            parsed[name] = reg(index) * scale
        parsed["PVTotalPower_low"] = sum(parsed.get(name, 0) for name in ("PV1Power", "PV2Power", "PV3Power", "PV4Power"))

    elif command_name == "PV5-8":
        fields = [
            ("PV5Power", 10),
            ("PV6Power", 10),
            ("PV7Power", 10),
            ("PV8Power", 10),
            ("PV5Voltage", 0.1),
            ("PV5Current", 0.1),
            ("PV6Voltage", 0.1),
            ("PV6Current", 0.1),
            ("PV7Voltage", 0.1),
            ("PV7Current", 0.1),
            ("PV8Voltage", 0.1),
            ("PV8Current", 0.1),
        ]
        for index, (name, scale) in enumerate(fields):
            parsed[name] = reg(index) * scale
        parsed["PVTotalPower_high"] = sum(parsed.get(name, 0) for name in ("PV5Power", "PV6Power", "PV7Power", "PV8Power"))

    elif command_name == "grid":
        parsed.update(
            {
                "inputPowerL1": _signed_16(reg(6)),
                "inputPowerL2": _signed_16(reg(7)),
                "inputPowerL3": _signed_16(reg(8)),
                "inputPowerTotal": _signed_16(reg(9)),
                "outTotalPower": _signed_16(reg(21)),
            }
        )

    elif command_name == "load":
        if len(registers) < 16:
            return None
        parsed["LoadTotalPower"] = _signed_32_from_registers(reg(9), reg(15))

    elif command_name == "gen":
        parsed["GenTotalPower"] = _signed_32_from_registers(reg(6), reg(10))

    elif command_name == "settings":
        parsed.update(
            {
                "batteryFloatVoltage": reg(0) * 0.1,
                "batteryWorkMode": {0: "voltage", 1: "capacity", 2: "no_battery"}.get(reg(10), reg(10)),
                "batteryVoltageShutdown": reg(17) * 0.1,
            }
        )
    else:
        return None

    return command_name, parsed


async def persist_deye_modbus_power_measurement_from_ws(
    *,
    device_aliases: Iterable[str],
    raw_event: dict[str, Any],
    measured_at: Optional[datetime] = None,
) -> bool:
    from backend.database.db import async_session_maker
    from backend.services.energy.modbus_cache import get_cor_agent_snapshot_cache

    aliases = {str(alias).strip() for alias in device_aliases if alias and str(alias).strip()}
    if not aliases:
        return False

    current_parsed = _parse_deye_modbus_read_event(raw_event)
    if current_parsed is None:
        return False

    measured_at = measured_at or datetime.utcnow()
    snapshot: dict[str, Any] = {}
    source_task_id = current_parsed[0]

    for alias in aliases:
        cache_payload = await get_cor_agent_snapshot_cache(alias)
        events = cache_payload.get("events") if isinstance(cache_payload, dict) else None
        if isinstance(events, dict):
            for event in events.values():
                if not isinstance(event, dict):
                    continue
                if not _is_event_fresh_for_history(event, measured_at):
                    continue
                parsed = _parse_deye_modbus_read_event(event)
                if parsed:
                    snapshot.update(parsed[1])

    snapshot.update(current_parsed[1])
    snapshot["measured_at"] = measured_at

    if "PVTotalPower" not in snapshot:
        pv_low = _as_float(snapshot.get("PVTotalPower_low")) or 0
        pv_high = _as_float(snapshot.get("PVTotalPower_high")) or 0
        if pv_low or pv_high:
            snapshot["PVTotalPower"] = pv_low + pv_high

    try:
        async with async_session_maker() as db:
            resolved_object = await _resolve_energetic_object_by_bridge_aliases(db, aliases)
            if resolved_object is None:
                return False

            object_id, object_name = resolved_object
            measurement = build_power_measurement_from_snapshot(
                object_id=object_id,
                object_name=object_name,
                snapshot=snapshot,
                source_protocol="cor_bridge_modbus_ws",
                source_task_id=source_task_id,
            )
            if measurement is None:
                return False

            await create_power_measurement(db, measurement)
            return True
    except Exception as exc:
        logger.error(
            f"Failed to persist Deye COR Bridge power measurement for aliases {sorted(aliases)}: {exc}",
            exc_info=True,
        )
        return False


async def _resolve_energetic_object_by_bridge_aliases(
    db: AsyncSession,
    aliases: set[str],
) -> tuple[str, str] | None:
    result = await db.execute(
        select(EnergeticObject.id, EnergeticObject.name)
        .where(or_(*(EnergeticObject.cor_bridges.any(alias) for alias in aliases)))
        .limit(1)
    )
    row = result.first()
    if row:
        return str(row[0]), str(row[1])

    device_result = await db.execute(
        select(EnergeticDevice.id, EnergeticDevice.device_id).where(
            or_(
                EnergeticDevice.id.in_(aliases),
                EnergeticDevice.device_id.in_(aliases),
            )
        )
    )
    bridge_refs = set(aliases)
    for device_id, device_code in device_result.all():
        if device_id:
            bridge_refs.add(str(device_id))
        if device_code:
            bridge_refs.add(str(device_code))

    fallback_result = await db.execute(
        select(EnergeticObject.id, EnergeticObject.name)
        .where(or_(*(EnergeticObject.cor_bridges.any(ref) for ref in bridge_refs)))
        .limit(1)
    )
    fallback_row = fallback_result.first()
    if fallback_row:
        return str(fallback_row[0]), str(fallback_row[1])

    return None


def measurement_to_cerbo_response(measurement: CerboMeasurement | PowerMeasurement) -> CerboMeasurementResponse:
    if isinstance(measurement, CerboMeasurement):
        return CerboMeasurementResponse.model_validate(measurement)

    raw_snapshot = measurement.raw_snapshot if isinstance(measurement.raw_snapshot, dict) else {}
    raw_soc = _battery_soc_from_snapshot(raw_snapshot)
    raw_load_power = _load_power_from_snapshot(raw_snapshot)
    raw_battery_voltage = _battery_voltage_from_snapshot(raw_snapshot)
    soc = raw_soc if _is_zero(measurement.battery_soc) and raw_soc not in (None, 0) else measurement.battery_soc
    load_power = (
        raw_load_power
        if _is_zero(measurement.load_power_w) and raw_load_power not in (None, 0)
        else measurement.load_power_w
    )
    battery_voltage = measurement.battery_voltage_v
    if battery_voltage is None:
        battery_voltage = raw_battery_voltage

    return CerboMeasurementResponse(
        id=measurement.id,
        created_at=measurement.created_at,
        measured_at=measurement.measured_at,
        object_name=measurement.object_name,
        general_battery_power=measurement.battery_power_w,
        inverter_total_ac_output=load_power,
        ess_total_input_power=measurement.grid_power_w,
        solar_total_pv_power=measurement.solar_power_w,
        battery_voltage=battery_voltage,
        soc=soc,
    )


async def get_power_history_paginated(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 10,
    energetic_object_id: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> PaginatedResponse[CerboMeasurementResponse]:
    offset = (page - 1) * page_size
    fetch_limit = offset + page_size

    cerbo_total_query = select(func.count()).select_from(CerboMeasurement).where(
        *_history_filters(CerboMeasurement, energetic_object_id, start_date, end_date)
    )
    power_total_query = select(func.count()).select_from(PowerMeasurement).where(
        *_history_filters(PowerMeasurement, energetic_object_id, start_date, end_date)
    )
    cerbo_total = (await db.execute(cerbo_total_query)).scalar_one()
    power_total = (await db.execute(power_total_query)).scalar_one()
    total_count = cerbo_total + power_total

    cerbo_query = (
        select(CerboMeasurement)
        .where(*_history_filters(CerboMeasurement, energetic_object_id, start_date, end_date))
        .order_by(CerboMeasurement.measured_at.desc())
        .limit(fetch_limit)
    )
    power_query = (
        select(PowerMeasurement)
        .where(*_history_filters(PowerMeasurement, energetic_object_id, start_date, end_date))
        .order_by(PowerMeasurement.measured_at.desc())
        .limit(fetch_limit)
    )

    cerbo_rows = (await db.execute(cerbo_query)).scalars().all()
    power_rows = (await db.execute(power_query)).scalars().all()
    rows = list(cerbo_rows) + list(power_rows)
    rows.sort(key=lambda row: row.measured_at, reverse=True)

    page_rows = rows[offset : offset + page_size]
    total_pages = (total_count + page_size - 1) // page_size if total_count else 0

    return PaginatedResponse(
        items=[measurement_to_cerbo_response(row) for row in page_rows],
        total_count=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


async def get_averaged_power_history(
    db: AsyncSession,
    *,
    energetic_object_id: str,
    start_date: datetime,
    end_date: datetime,
    intervals: int = 60,
) -> list[CerboMeasurementResponse]:
    if intervals <= 0:
        raise ValueError("intervals must be greater than 0")
    if end_date <= start_date:
        raise ValueError("end_date must be greater than start_date")

    rows = await _load_history_rows(
        db,
        energetic_object_id=energetic_object_id,
        start_date=start_date,
        end_date=end_date,
    )
    if not rows:
        return []

    interval_size = (end_date - start_date) / intervals
    grouped = [[] for _ in range(intervals)]
    for row in rows:
        idx = min(int((row.measured_at - start_date) / interval_size), intervals - 1)
        grouped[idx].append(measurement_to_cerbo_response(row))

    result: list[CerboMeasurementResponse] = []
    for idx, measurements in enumerate(grouped):
        if not measurements:
            continue

        def avg(field: str) -> float | None:
            values = [getattr(item, field) for item in measurements if getattr(item, field) is not None]
            return sum(values) / len(values) if values else None

        base = measurements[0]
        result.append(
            CerboMeasurementResponse(
                id=base.id,
                created_at=base.created_at,
                measured_at=start_date + idx * interval_size,
                object_name=base.object_name,
                general_battery_power=avg("general_battery_power"),
                inverter_total_ac_output=avg("inverter_total_ac_output"),
                ess_total_input_power=avg("ess_total_input_power"),
                solar_total_pv_power=avg("solar_total_pv_power"),
                soc=avg("soc"),
            )
        )

    return result


async def get_energy_power_history(
    db: AsyncSession,
    *,
    energetic_object_id: str,
    start_date: datetime,
    end_date: datetime,
    interval_minutes: int = 30,
) -> dict:
    if end_date <= start_date:
        raise ValueError("end_date must be greater than start_date")

    rounded_start = start_date.replace(minute=0, second=0, microsecond=0)
    if end_date.minute == 0 and end_date.second == 0 and end_date.microsecond == 0:
        rounded_end = end_date
    else:
        rounded_end = end_date.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    rows = await _load_history_rows(
        db,
        energetic_object_id=energetic_object_id,
        start_date=rounded_start,
        end_date=rounded_end,
    )
    measurements = [measurement_to_cerbo_response(row) for row in rows]
    measurements.sort(key=lambda row: row.measured_at)

    current_interval_start = rounded_start
    intervals = []
    while current_interval_start < rounded_end:
        current_interval_end = current_interval_start + timedelta(minutes=interval_minutes)
        intervals.append(
            {
                "start": current_interval_start,
                "end": current_interval_end,
                "measurements": [],
                "measurement_count": 0,
                "has_sufficient_data": False,
            }
        )
        current_interval_start = current_interval_end

    for measurement in measurements:
        for interval in intervals:
            if interval["start"] <= measurement.measured_at < interval["end"]:
                interval["measurements"].append(measurement)
                interval["measurement_count"] += 1
                break

    return _build_energy_response(intervals, measurements)


def _history_filters(
    model: type[CerboMeasurement] | type[PowerMeasurement],
    energetic_object_id: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> list:
    filters = [model.energetic_object_id == energetic_object_id]
    if start_date:
        filters.append(model.measured_at >= start_date)
    if end_date:
        filters.append(model.measured_at <= end_date)
    return filters


async def _load_history_rows(
    db: AsyncSession,
    *,
    energetic_object_id: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> list[CerboMeasurement | PowerMeasurement]:
    cerbo_query = select(CerboMeasurement).where(
        *_history_filters(CerboMeasurement, energetic_object_id, start_date, end_date)
    )
    power_query = select(PowerMeasurement).where(
        *_history_filters(PowerMeasurement, energetic_object_id, start_date, end_date)
    )

    cerbo_rows = (await db.execute(cerbo_query)).scalars().all()
    power_rows = (await db.execute(power_query)).scalars().all()
    return list(cerbo_rows) + list(power_rows)


def _build_energy_response(
    intervals: list[dict],
    all_measurements: Iterable[CerboMeasurementResponse],
) -> dict:
    all_measurements = list(all_measurements)
    results = []
    for interval in intervals:
        measurements = interval["measurements"]
        interval["has_sufficient_data"] = len(measurements) >= 3

        if len(measurements) < 2:
            results.append(
                {
                    "interval_start": interval["start"],
                    "interval_end": interval["end"],
                    "solar_energy_kwh": 0.0,
                    "load_energy_kwh": 0.0,
                    "grid_energy_kwh": 0.0,
                    "battery_energy_kwh": 0.0,
                    "measurement_count": interval["measurement_count"],
                    "has_sufficient_data": interval["has_sufficient_data"],
                }
            )
            continue

        solar_energy, load_energy, grid_energy, battery_energy = _integrate_measurements(measurements)
        results.append(
            {
                "interval_start": interval["start"],
                "interval_end": interval["end"],
                "solar_energy_kwh": round(solar_energy, 3),
                "load_energy_kwh": round(load_energy, 3),
                "grid_energy_kwh": round(grid_energy, 3),
                "battery_energy_kwh": round(battery_energy, 3),
                "measurement_count": interval["measurement_count"],
                "has_sufficient_data": interval["has_sufficient_data"],
            }
        )

    total_solar, total_load, _, total_battery = _integrate_measurements(all_measurements)
    total_grid_import, total_grid_export = _integrate_grid_import_export(all_measurements)

    return {
        "intervals": results,
        "totals": {
            "solar_energy_total": round(total_solar, 0),
            "load_energy_total": round(total_load, 0),
            "grid_import_total": round(total_grid_import, 0),
            "grid_export_total": round(total_grid_export, 0),
            "battery_energy_total": round(total_battery, 0),
        },
    }


def _integrate_measurements(measurements: Iterable[CerboMeasurementResponse]) -> Tuple[float, float, float, float]:
    rows = list(measurements)
    solar_energy = 0.0
    load_energy = 0.0
    grid_energy = 0.0
    battery_energy = 0.0

    for idx in range(1, len(rows)):
        prev = rows[idx - 1]
        curr = rows[idx]
        delta_h = (curr.measured_at - prev.measured_at).total_seconds() / 3600.0
        if delta_h <= 0:
            continue
        if prev.solar_total_pv_power is not None:
            solar_energy += (prev.solar_total_pv_power / 1000.0) * delta_h
        if prev.inverter_total_ac_output is not None:
            load_energy += (prev.inverter_total_ac_output / 1000.0) * delta_h
        if prev.ess_total_input_power is not None:
            grid_energy += (prev.ess_total_input_power / 1000.0) * delta_h
        if prev.general_battery_power is not None:
            battery_energy += (prev.general_battery_power / 1000.0) * delta_h

    return solar_energy, load_energy, grid_energy, battery_energy


def _integrate_grid_import_export(measurements: Iterable[CerboMeasurementResponse]) -> Tuple[float, float]:
    rows = list(measurements)
    grid_import = 0.0
    grid_export = 0.0

    for idx in range(1, len(rows)):
        prev = rows[idx - 1]
        curr = rows[idx]
        delta_h = (curr.measured_at - prev.measured_at).total_seconds() / 3600.0
        if delta_h <= 0 or prev.ess_total_input_power is None:
            continue

        grid_q = (prev.ess_total_input_power / 1000.0) * delta_h
        if grid_q >= 0:
            grid_import += grid_q
        else:
            grid_export += abs(grid_q)

    return grid_import, grid_export
