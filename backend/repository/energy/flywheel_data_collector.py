from datetime import datetime
import json
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.redis_db import redis_client
from backend.database.models import EnergeticDevice, EnergeticObject, FlywheelMeasurements



# Момент инерции маховика (кг·м²)
FLYWHEEL_J = 1.9075

# Коэффициент перевода (об/мин)^2 -> Вт·ч
RPM2_WH = (FLYWHEEL_J * 3.141592653589793 ** 2) / (2 * 900 * 3600)

_REQUIRED_FLYWHEEL_EVENTS = {
    "fw_status_response",
    "fw_dc_status_response",
    "fw_motor_status_response",
}

_RAW_TO_NORMALIZED_EVENT = {
    "fw_status": "fw_status_response",
    "fw_status_response": "fw_status_response",
    "fwstatus": "fw_status_response",
    "fw_dc_status": "fw_dc_status_response",
    "fw_dc_status_response": "fw_dc_status_response",
    "fwdcstatus": "fw_dc_status_response",
    "fw_motor_status": "fw_motor_status_response",
    "fw_motor_status_response": "fw_motor_status_response",
    "fwmstatus": "fw_motor_status_response",
}

_PI30_TO_NORMALIZED_EVENT = {
    "FWSTATUS": "fw_status_response",
    "FWDCSTATUS": "fw_dc_status_response",
    "FWMSTATUS": "fw_motor_status_response",
}

FLYWHEEL_AGGREGATE_CACHE_TTL_SECONDS = 120


def _aggregate_cache_key(cache_key: str) -> str:
    return f"flywheel:aggregate:{cache_key}"


async def _load_aggregate_state(cache_key: str) -> Dict[str, Any]:
    key = _aggregate_cache_key(cache_key)
    try:
        raw = await redis_client.get(key)
    except Exception:
        raw = None

    if not raw:
        return {"parts": {}, "last_signature": None}

    try:
        payload = json.loads(raw)
    except Exception:
        return {"parts": {}, "last_signature": None}

    if not isinstance(payload, dict):
        return {"parts": {}, "last_signature": None}

    parts = payload.get("parts")
    if not isinstance(parts, dict):
        parts = {}

    return {
        "parts": parts,
        "last_signature": payload.get("last_signature"),
    }


async def _save_aggregate_state(cache_key: str, state: Dict[str, Any]) -> None:
    key = _aggregate_cache_key(cache_key)
    payload = {
        "parts": state.get("parts", {}),
        "last_signature": state.get("last_signature"),
        "updated_at": datetime.utcnow().isoformat(),
    }
    await redis_client.setex(
        key,
        FLYWHEEL_AGGREGATE_CACHE_TTL_SECONDS,
        json.dumps(payload, ensure_ascii=False),
    )


def compute_energy_wh(rpm: float) -> float:
    if not isinstance(rpm, (int, float)) or rpm < 0:
        return 0.0
    energy = RPM2_WH * rpm * rpm
    return round(energy, 2)


def _extract_hex_and_command(raw_message: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    nested = raw_message.get("data") if isinstance(raw_message.get("data"), dict) else raw_message
    command_name = (
        nested.get("command_name")
        or raw_message.get("command_name")
        or nested.get("command_type")
        or raw_message.get("command_type")
        or nested.get("cmd")
        or raw_message.get("cmd")
    )
    pi30_command = (
        nested.get("pi30_command")
        or raw_message.get("pi30_command")
        or nested.get("pi30")
        or raw_message.get("pi30")
    )
    hex_data = (
        nested.get("hex_data")
        or raw_message.get("hex_data")
        or nested.get("hex_response")
        or raw_message.get("hex_response")
    )

    normalized = None
    if command_name:
        normalized = _RAW_TO_NORMALIZED_EVENT.get(str(command_name).strip().lower())
    if not normalized and pi30_command:
        pi30_command_text = str(pi30_command).strip()
        normalized = (
            _PI30_TO_NORMALIZED_EVENT.get(pi30_command_text.upper())
            or _RAW_TO_NORMALIZED_EVENT.get(pi30_command_text.lower())
        )

    return normalized, str(hex_data) if hex_data else None


def _parse_pi30_ascii_values(hex_data: str) -> Optional[List[str]]:
    try:
        clean_hex = "".join(str(hex_data).split())
        payload = bytes.fromhex(clean_hex)
    except ValueError:
        return None

    if len(payload) < 3 or payload[-1] != 0x0D:
        return None

    ascii_payload = payload[:-3].decode("utf-8", errors="ignore")
    if not ascii_payload.startswith("("):
        return None

    return ascii_payload[1:].strip().split()


def _parse_event_payload(normalized_event: str, values: List[str]) -> Optional[Dict[str, Any]]:
    if normalized_event == "fw_status_response":
        if len(values) < 5:
            return None
        temperatures = [float(values[i]) for i in range(4)]
        status_byte = int(values[4])
        return {
            "stator_temperature": temperatures,
            "status_byte": status_byte,
            "start_active": bool(status_byte & 1),
            "delay_expired": bool(status_byte & 2),
            "gen_mode": bool(status_byte & 4),
        }

    if normalized_event == "fw_motor_status_response":
        if len(values) < 5:
            return None
        return {
            "flywheel_speed_rpm": float(int(values[0])),
            "flywheel_pwm": float(int(values[1])),
            "flywheel_frequency": float(int(values[2])),
            "flywheel_arr_timer": float(int(values[3])),
            "flywheel_pwm_raw": float(int(values[4])),
        }

    if normalized_event == "fw_dc_status_response":
        if len(values) < 3:
            return None
        return {
            "flywheel_voltage": float(values[0]),
            "flywheel_current": float(values[1]),
            "flywheel_power": float(values[2]),
        }

    return None


async def _resolve_energetic_object_id_by_device_aliases(
    db: AsyncSession,
    device_aliases: Set[str],
) -> Optional[str]:
    aliases = [alias for alias in device_aliases if alias]
    if not aliases:
        return None

    result = await db.execute(
        select(EnergeticObject.id).where(
            or_(*(EnergeticObject.cor_bridges.any(alias) for alias in aliases))
        ).limit(1)
    )
    object_id = result.scalar_one_or_none()
    if object_id:
        return str(object_id)

    device_result = await db.execute(
        select(EnergeticDevice.id, EnergeticDevice.device_id).where(
            or_(
                EnergeticDevice.id.in_(aliases),
                EnergeticDevice.device_id.in_(aliases),
            )
        )
    )
    device_rows = device_result.all()
    bridge_refs: Set[str] = set(aliases)
    for device_id, device_code in device_rows:
        if device_id:
            bridge_refs.add(str(device_id))
        if device_code:
            bridge_refs.add(str(device_code))

    if not bridge_refs:
        return None

    fallback_result = await db.execute(
        select(EnergeticObject.id).where(
            or_(*(EnergeticObject.cor_bridges.any(ref) for ref in bridge_refs))
        ).limit(1)
    )
    fallback_object_id = fallback_result.scalar_one_or_none()
    return str(fallback_object_id) if fallback_object_id else None


async def try_collect_flywheel_measurement(
    db: AsyncSession,
    *,
    cache_key: str,
    device_aliases: Set[str],
    raw_event: Dict[str, Any],
    measured_at: Optional[datetime] = None,
) -> Optional[FlywheelMeasurements]:
    normalized_event, hex_data = _extract_hex_and_command(raw_event)
    if not normalized_event or not hex_data:
        return None

    values = _parse_pi30_ascii_values(hex_data)
    if values is None:
        return None

    parsed_part = _parse_event_payload(normalized_event, values)
    if parsed_part is None:
        return None

    aggregate_state = await _load_aggregate_state(cache_key)
    parts = aggregate_state["parts"]
    parts[normalized_event] = parsed_part
    await _save_aggregate_state(cache_key, aggregate_state)

    if not _REQUIRED_FLYWHEEL_EVENTS.issubset(set(parts.keys())):
        return None

    signature = json.dumps(
        {
            key: parts[key]
            for key in sorted(_REQUIRED_FLYWHEEL_EVENTS)
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    if signature == aggregate_state.get("last_signature"):
        return None

    energetic_object_id = await _resolve_energetic_object_id_by_device_aliases(db, device_aliases)
    if not energetic_object_id:
        return None

    status_part = parts["fw_status_response"]
    motor_part = parts["fw_motor_status_response"]
    dc_part = parts["fw_dc_status_response"]

    record = FlywheelMeasurements(
        energetic_object_id=energetic_object_id,
        measured_at=measured_at or datetime.utcnow(),
        flywheel_speed_rpm=motor_part["flywheel_speed_rpm"],
        flywheel_pwm=motor_part["flywheel_pwm"],
        flywheel_frequency=motor_part["flywheel_frequency"],
        flywheel_arr_timer=motor_part["flywheel_arr_timer"],
        flywheel_pwm_raw=motor_part["flywheel_pwm_raw"],
        flywheel_voltage=dc_part["flywheel_voltage"],
        flywheel_current=dc_part["flywheel_current"],
        flywheel_power=dc_part["flywheel_power"],
        stator_temperature=status_part["stator_temperature"],
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    aggregate_state["last_signature"] = signature
    await _save_aggregate_state(cache_key, aggregate_state)
    return record


async def get_flywheel_measurements(db: AsyncSession, 
                                    energetic_object_id: str, 
                                    skip: int = 1, 
                                    limit: int = 100, 
                                    start_time: Optional[datetime] = None, 
                                    end_time: Optional[datetime] = None) -> Tuple[List[FlywheelMeasurements], int]:
    query = select(FlywheelMeasurements).where(FlywheelMeasurements.energetic_object_id == energetic_object_id)
    if start_time:
        query = query.where(FlywheelMeasurements.measured_at >= start_time)
    if end_time:
        query = query.where(FlywheelMeasurements.measured_at <= end_time)
    
    offset = (skip - 1) * limit
    query = (
        query.offset(offset)
        .limit(limit)
        .order_by(FlywheelMeasurements.measured_at.desc())
    )

    result = await db.execute(query)
    measurements = result.scalars().all()

    count_query = select(func.count()).select_from(FlywheelMeasurements).where(FlywheelMeasurements.energetic_object_id == energetic_object_id)
    if start_time:
        count_query = count_query.where(FlywheelMeasurements.measured_at >= start_time)
    if end_time:
        count_query = count_query.where(FlywheelMeasurements.measured_at <= end_time)

    total_count_result = await db.execute(count_query)
    total_count = total_count_result.scalar_one()

    return measurements, total_count
