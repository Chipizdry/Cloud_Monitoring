"""Persistence of TAC4300CT readings received from COR Bridge polling."""

from __future__ import annotations

import math
import struct
from datetime import datetime, timezone
from typing import Any, Iterable

from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import EnergeticObject, EnergyMeterMeasurement
from backend.schemas.device_measurement import EnergyMeterMeasurementResponse, PaginatedResponse
from backend.services.energy.cor_bridge_broadcast_presets import calculate_modbus_rtu_crc


TAC_EVENT_MAX_AGE_SECONDS = 15
TAC_BLOCKS = {
    "phases": (0x0000, 30),
    "angles": (0x001E, 30),
    "energy32": (0x0400, 56),
    "energy_float": (0x0500, 56),
}


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_tac_event(event: dict[str, Any]) -> tuple[str, int, dict[str, float]] | None:
    message = event.get("data") if isinstance(event.get("data"), dict) else event
    suffix = str(message.get("command_name") or event.get("command_name") or "").strip().lower()
    block = TAC_BLOCKS.get(suffix)
    if block is None:
        return None

    hex_response = message.get("hex_response") or event.get("hex_response")
    if not isinstance(hex_response, str):
        return None
    try:
        frame = bytes.fromhex(hex_response)
    except ValueError:
        return None
    start, register_count = block
    if (
        len(frame) != register_count * 2 + 5
        or frame[1] != 4
        or frame[2] != register_count * 2
        or calculate_modbus_rtu_crc(frame[:-2]) != frame[-2:]
    ):
        return None

    data = frame[3:-2]

    def read(address: int, kind: str = "f") -> float:
        value = struct.unpack_from(">" + kind, data, (address - start) * 2)[0]
        return float(value)

    if suffix == "phases":
        values = {
            "voltage_l1_n_v": read(0x0000),
            "voltage_l2_n_v": read(0x0002),
            "voltage_l3_n_v": read(0x0004),
        }
    elif suffix == "angles":
        values = {
            "voltage_l1_l2_v": read(0x002A),
            "voltage_l2_l3_v": read(0x002C),
            "voltage_l3_l1_v": read(0x002E),
            "frequency_hz": read(0x0030),
            "active_power_kw": read(0x0032) / 1000,
            "reactive_power_kvar": read(0x0034) / 1000,
            "apparent_power_kva": read(0x0036) / 1000,
            "power_factor": read(0x0038),
            "angle_deg": read(0x003A),
        }
    elif suffix == "energy_float":
        values = {"apparent_energy_kvah": read(0x0510)}
    else:
        values = {"apparent_energy_kvah": read(0x0410, "I") / 100}

    if not all(math.isfinite(value) for value in values.values()):
        return None
    return suffix, frame[0], values


def build_tac4300ct_reading(
    events: Iterable[dict[str, Any]],
    *,
    measured_at: datetime,
    slave_id: int,
) -> dict[str, float] | None:
    """Join fresh Modbus blocks for one slave into a complete, scaled reading."""
    blocks: dict[str, tuple[datetime, dict[str, float]]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        event_time = _timestamp(event.get("timestamp"))
        if event_time is None or abs((measured_at - event_time).total_seconds()) > TAC_EVENT_MAX_AGE_SECONDS:
            continue
        parsed = _parse_tac_event(event)
        if parsed is None or parsed[1] != slave_id:
            continue
        suffix, _, values = parsed
        if suffix not in blocks or event_time > blocks[suffix][0]:
            blocks[suffix] = (event_time, values)

    if "phases" not in blocks or "angles" not in blocks:
        return None
    energy = blocks.get("energy_float") or blocks.get("energy32")
    if energy is None:
        return None
    return {**blocks["phases"][1], **blocks["angles"][1], **energy[1]}


async def persist_tac4300ct_measurement_from_ws(
    *,
    device_aliases: Iterable[str],
    raw_event: dict[str, Any],
    measured_at: datetime | None = None,
) -> bool:
    """Write one complete reading for each fresh angles response."""
    current = _parse_tac_event(raw_event)
    if current is None or current[0] != "angles":
        return False

    from backend.database.db import async_session_maker
    from backend.services.energy.modbus_cache import get_cor_agent_snapshot_cache

    measured_at = measured_at or datetime.utcnow()
    slave_id = current[1]
    aliases = {str(alias).strip() for alias in device_aliases if alias and str(alias).strip()}
    if not aliases:
        return False

    try:
        events = [{"data": raw_event, "timestamp": measured_at.isoformat()}]
        for alias in aliases:
            snapshot = await get_cor_agent_snapshot_cache(alias)
            cached = snapshot.get("events") if isinstance(snapshot, dict) else None
            if isinstance(cached, dict):
                events.extend(value for value in cached.values() if isinstance(value, dict))
        reading = build_tac4300ct_reading(events, measured_at=measured_at, slave_id=slave_id)
        if reading is None:
            return False

        async with async_session_maker() as db:
            result = await db.execute(
                select(EnergeticObject).where(
                    EnergeticObject.vendor.ilike("Taiye"),
                    EnergeticObject.model_name.ilike("TAC4300CT"),
                    or_(*(EnergeticObject.cor_bridges.any(alias) for alias in aliases)),
                )
            )
            objects = [
                obj for obj in result.scalars().all()
                if int(obj.slave_ids[0] if obj.slave_ids else 1) == slave_id
            ]
            if len(objects) != 1:
                if len(objects) > 1:
                    logger.warning(f"Ambiguous TAC4300CT object for aliases {sorted(aliases)}, slave {slave_id}")
                return False

            db.add(EnergyMeterMeasurement(
                energetic_object_id=objects[0].id,
                measured_at=measured_at,
                **reading,
            ))
            await db.commit()
            return True
    except Exception as exc:
        logger.error(f"Failed to persist TAC4300CT measurement for {sorted(aliases)}: {exc}", exc_info=True)
        return False


async def get_energy_meter_history_paginated(
    db: AsyncSession,
    *,
    energetic_object_id: str,
    page: int = 1,
    page_size: int = 10,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> PaginatedResponse[EnergyMeterMeasurementResponse]:
    filters = [EnergyMeterMeasurement.energetic_object_id == energetic_object_id]
    if start_date is not None:
        filters.append(EnergyMeterMeasurement.measured_at >= start_date)
    if end_date is not None:
        filters.append(EnergyMeterMeasurement.measured_at <= end_date)

    count_result = await db.execute(
        select(func.count()).select_from(EnergyMeterMeasurement).where(*filters)
    )
    total_count = count_result.scalar_one()
    rows_result = await db.execute(
        select(EnergyMeterMeasurement)
        .where(*filters)
        .order_by(EnergyMeterMeasurement.measured_at.desc(), EnergyMeterMeasurement.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = rows_result.scalars().all()
    return PaginatedResponse(
        items=[EnergyMeterMeasurementResponse.model_validate(row) for row in rows],
        total_count=total_count,
        page=page,
        page_size=page_size,
        total_pages=(total_count + page_size - 1) // page_size if total_count else 0,
    )
