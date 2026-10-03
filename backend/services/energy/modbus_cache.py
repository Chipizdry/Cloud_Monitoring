import json
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from backend.database.redis_db import redis_client


MODBUS_CACHE_TTL_SECONDS = 30
MODBUS_CACHE_KEY_PREFIX = "modbus:cache"
POLLING_SNAPSHOT_TTL_SECONDS = 120
COR_AGENT_SNAPSHOT_TTL_SECONDS = 120
PI30_PARSED_CACHE_TTL_SECONDS = 120


def _make_register_index_key(
    protocol: str,
    host: str,
    port: int,
    slave_id: int,
    func_code: int,
    object_id: Optional[str] = None,
) -> str:
    object_part = object_id or "-"
    return (
        f"{MODBUS_CACHE_KEY_PREFIX}:registers:index:"
        f"{protocol}:{host}:{_safe_int(port, 502)}:{object_part}:{_safe_int(slave_id, 1)}:{_safe_int(func_code, 3)}"
    )


def _safe_int(value: Optional[int], default: int = 0) -> int:
    if value is None:
        return default
    return int(value)


def make_register_cache_key(
    protocol: str,
    host: str,
    port: int,
    slave_id: int,
    start: int,
    count: int,
    func_code: int,
    object_id: Optional[str] = None,
) -> str:
    object_part = object_id or "-"
    return (
        f"{MODBUS_CACHE_KEY_PREFIX}:registers:"
        f"{protocol}:{host}:{_safe_int(port, 502)}:{object_part}:{_safe_int(slave_id, 1)}:"
        f"{_safe_int(start)}:{_safe_int(count)}:{_safe_int(func_code, 3)}"
    )


def make_coils_cache_key(
    protocol: str,
    host: str,
    port: int,
    slave_id: int,
    start: int,
    count: int,
    object_id: Optional[str] = None,
) -> str:
    object_part = object_id or "-"
    return (
        f"{MODBUS_CACHE_KEY_PREFIX}:coils:"
        f"{protocol}:{host}:{_safe_int(port, 502)}:{object_part}:{_safe_int(slave_id, 1)}:"
        f"{_safe_int(start)}:{_safe_int(count)}"
    )


def make_discrete_inputs_cache_key(
    protocol: str,
    host: str,
    port: int,
    slave_id: int,
    start: int,
    count: int,
    object_id: Optional[str] = None,
) -> str:
    object_part = object_id or "-"
    return (
        f"{MODBUS_CACHE_KEY_PREFIX}:discrete_inputs:"
        f"{protocol}:{host}:{_safe_int(port, 502)}:{object_part}:{_safe_int(slave_id, 1)}:"
        f"{_safe_int(start)}:{_safe_int(count)}"
    )


async def set_modbus_cache(
    key: str,
    data: List[Any],
    ttl_seconds: int = MODBUS_CACHE_TTL_SECONDS,
) -> None:
    payload = {
        "ok": True,
        "data": data,
        "cached_at": int(time.time()),
    }

    try:
        await redis_client.setex(key, int(ttl_seconds), json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        logger.warning(f"Failed to write Modbus cache for key={key}: {e}")


async def set_modbus_register_cache(
    protocol: str,
    host: str,
    port: int,
    slave_id: int,
    start: int,
    count: int,
    func_code: int,
    data: List[Any],
    object_id: Optional[str] = None,
    ttl_seconds: int = MODBUS_CACHE_TTL_SECONDS,
) -> str:
    key = make_register_cache_key(
        protocol=protocol,
        host=host,
        port=port,
        slave_id=slave_id,
        start=start,
        count=count,
        func_code=func_code,
        object_id=object_id,
    )

    await set_modbus_cache(key=key, data=data, ttl_seconds=ttl_seconds)

    index_key = _make_register_index_key(
        protocol=protocol,
        host=host,
        port=port,
        slave_id=slave_id,
        func_code=func_code,
        object_id=object_id,
    )
    range_field = f"{_safe_int(start)}:{_safe_int(count)}"

    try:
        await redis_client.hset(index_key, range_field, key)
        await redis_client.expire(index_key, int(ttl_seconds))
    except Exception as e:
        logger.warning(f"Failed to update Modbus register index for key={index_key}: {e}")

    return key


async def get_modbus_cache(
    key: str,
    max_age_seconds: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    try:
        raw = await redis_client.get(key)
        if not raw:
            return None

        payload = json.loads(raw)
        cached_at = int(payload.get("cached_at", 0))
        if max_age_seconds is not None and max_age_seconds > 0 and cached_at > 0:
            age = int(time.time()) - cached_at
            if age > int(max_age_seconds):
                return None

        return payload
    except Exception as e:
        logger.warning(f"Failed to read Modbus cache for key={key}: {e}")
        return None


async def get_modbus_register_cache(
    protocol: str,
    host: str,
    port: int,
    slave_id: int,
    start: int,
    count: int,
    func_code: int,
    object_id: Optional[str] = None,
    max_age_seconds: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    req_start = _safe_int(start)
    req_count = _safe_int(count)
    req_end = req_start + req_count

    exact_key = make_register_cache_key(
        protocol=protocol,
        host=host,
        port=port,
        slave_id=slave_id,
        start=start,
        count=count,
        func_code=func_code,
        object_id=object_id,
    )
    best_candidate: Optional[Dict[str, Any]] = None

    exact = await get_modbus_cache(exact_key, max_age_seconds=max_age_seconds)
    if exact:
        exact_data = exact.get("data")
        if isinstance(exact_data, list) and len(exact_data) >= req_count:
            best_candidate = {
                "ok": True,
                "data": exact_data[:req_count],
                "cached_at": exact.get("cached_at"),
                "range_source": "exact",
            }

    index_key = _make_register_index_key(
        protocol=protocol,
        host=host,
        port=port,
        slave_id=slave_id,
        func_code=func_code,
        object_id=object_id,
    )

    try:
        ranges = await redis_client.hgetall(index_key)
    except Exception as e:
        logger.warning(f"Failed to read Modbus register index {index_key}: {e}")
        return best_candidate

    if not ranges:
        return best_candidate

    candidates: List[tuple[int, int, str]] = []
    indexed_ranges: List[tuple[int, int, str]] = []
    for range_field, cache_key in ranges.items():
        try:
            s_raw, c_raw = str(range_field).split(":", 1)
            candidate_start = int(s_raw)
            candidate_count = int(c_raw)
            candidate_end = candidate_start + candidate_count
            indexed_ranges.append((candidate_start, candidate_count, cache_key))
            if candidate_start <= req_start and req_end <= candidate_end:
                candidates.append((candidate_count, candidate_start, cache_key))
        except Exception:
            continue

    candidates.sort(key=lambda item: (item[0], abs(item[1] - req_start)))

    for candidate_count, candidate_start, cache_key in candidates:
        payload = await get_modbus_cache(cache_key, max_age_seconds=max_age_seconds)
        if not payload:
            continue

        data = payload.get("data")
        if not isinstance(data, list):
            continue

        offset = req_start - candidate_start
        if offset < 0 or (offset + req_count) > len(data):
            continue

        sliced = data[offset: offset + req_count]
        current_candidate = {
            "ok": True,
            "data": sliced,
            "cached_at": payload.get("cached_at"),
            "range_source": f"cover:{candidate_start}:{candidate_count}",
        }

        if not best_candidate:
            best_candidate = current_candidate
            continue

        best_cached_at = _safe_int(best_candidate.get("cached_at"), 0)
        current_cached_at = _safe_int(current_candidate.get("cached_at"), 0)
        if current_cached_at > best_cached_at:
            best_candidate = current_candidate

    if best_candidate:
        return best_candidate

    # A polling preset may split a logical UI range into adjacent Modbus reads.
    # Assemble such a range from fresh indexed entries before falling back to live
    # I/O in the cache-first endpoint.
    segments: List[tuple[int, int, List[Any], int]] = []
    for candidate_start, candidate_count, cache_key in indexed_ranges:
        candidate_end = candidate_start + candidate_count
        if candidate_end <= req_start or candidate_start >= req_end:
            continue

        payload = await get_modbus_cache(cache_key, max_age_seconds=max_age_seconds)
        if not payload:
            continue

        data = payload.get("data")
        if not isinstance(data, list) or len(data) < candidate_count:
            continue

        segments.append(
            (
                candidate_start,
                candidate_end,
                data,
                _safe_int(payload.get("cached_at"), 0),
            )
        )

    assembled: List[Any] = []
    range_sources: List[str] = []
    cached_at_values: List[int] = []
    cursor = req_start
    while cursor < req_end:
        matching_segments = [
            segment for segment in segments
            if segment[0] <= cursor < segment[1]
        ]
        if not matching_segments:
            return None

        segment_start, segment_end, segment_data, segment_cached_at = max(
            matching_segments,
            key=lambda segment: (segment[1], segment[3]),
        )
        take_until = min(segment_end, req_end)
        offset = cursor - segment_start
        assembled.extend(segment_data[offset: offset + (take_until - cursor)])
        range_sources.append(f"{segment_start}:{segment_end - segment_start}")
        cached_at_values.append(segment_cached_at)
        cursor = take_until

    return {
        "ok": True,
        "data": assembled,
        # The oldest constituent block determines freshness of the composite.
        "cached_at": min(cached_at_values) if cached_at_values else None,
        "range_source": f"composite:{','.join(range_sources)}",
    }


def make_polling_snapshot_cache_key(object_id: str) -> str:
    return f"{MODBUS_CACHE_KEY_PREFIX}:polling_snapshot:{object_id}"


async def set_polling_snapshot_cache(
    object_id: str,
    data: Dict[str, Any],
    measured_at: Optional[str] = None,
    channel_event: Optional[Dict[str, Any]] = None,
    ttl_seconds: int = POLLING_SNAPSHOT_TTL_SECONDS,
) -> str:
    key = make_polling_snapshot_cache_key(object_id)
    payload: Dict[str, Any] = {
        "ok": True,
        "energetic_object_id": object_id,
        "data": data,
        "cached_at": int(time.time()),
    }
    if measured_at:
        payload["measured_at"] = measured_at
    if channel_event:
        payload["channel_event"] = channel_event

    try:
        await redis_client.setex(key, int(ttl_seconds), json.dumps(payload, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning(f"Failed to write polling snapshot cache for object_id={object_id}: {e}")

    return key


async def get_polling_snapshot_cache(
    object_id: str,
    max_age_seconds: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    key = make_polling_snapshot_cache_key(object_id)
    return await get_modbus_cache(key, max_age_seconds=max_age_seconds)


def make_cor_agent_snapshot_cache_key(device_id: str) -> str:
    return f"{MODBUS_CACHE_KEY_PREFIX}:cor_agent_snapshot:{device_id}"


def make_pi30_parsed_cache_key(device_id: str) -> str:
    return f"{MODBUS_CACHE_KEY_PREFIX}:pi30_parsed:{device_id}"


async def set_cor_agent_snapshot_cache(
    device_id: str,
    event: Dict[str, Any],
    ttl_seconds: int = COR_AGENT_SNAPSHOT_TTL_SECONDS,
) -> str:
    key = make_cor_agent_snapshot_cache_key(device_id)
    existing = await get_modbus_cache(key)

    payload: Dict[str, Any] = existing or {
        "ok": True,
        "device_id": device_id,
        "events": {},
        "cached_at": int(time.time()),
    }

    events = payload.get("events")
    if not isinstance(events, dict):
        events = {}

    message = event.get("data") if isinstance(event.get("data"), dict) else {}
    explicit_cmd = event.get("cmd") or message.get("cmd")
    raw_command_name = event.get("command_name") or message.get("command_name")
    normalized_command_name = str(raw_command_name or "").strip()

    command_name = (
        normalized_command_name if normalized_command_name and normalized_command_name.upper() != "UNKNOWN" else None
    ) or explicit_cmd or "unknown"
    if message.get("command_type") == "settings_response":
        command_name = f"settings_response:{message.get('category') or 'unknown'}"
    events[command_name] = event

    payload["ok"] = True
    payload["device_id"] = device_id
    payload["events"] = events
    payload["cached_at"] = int(time.time())

    try:
        await redis_client.setex(key, int(ttl_seconds), json.dumps(payload, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning(f"Failed to write COR-agent snapshot cache for device_id={device_id}: {e}")

    return key


async def get_cor_agent_snapshot_cache(
    device_id: str,
    max_age_seconds: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    key = make_cor_agent_snapshot_cache_key(device_id)
    return await get_modbus_cache(key, max_age_seconds=max_age_seconds)


async def set_pi30_parsed_cache(
    device_id: str,
    parsed_event: Dict[str, Any],
    ttl_seconds: int = PI30_PARSED_CACHE_TTL_SECONDS,
) -> str:
    key = make_pi30_parsed_cache_key(device_id)
    payload = {
        "ok": True,
        "device_id": device_id,
        "data": parsed_event,
        "cached_at": int(time.time()),
    }

    try:
        await redis_client.setex(key, int(ttl_seconds), json.dumps(payload, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning(f"Failed to write PI30 parsed cache for device_id={device_id}: {e}")

    return key


async def get_pi30_parsed_cache(
    device_id: str,
    max_age_seconds: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    key = make_pi30_parsed_cache_key(device_id)
    return await get_modbus_cache(key, max_age_seconds=max_age_seconds)
