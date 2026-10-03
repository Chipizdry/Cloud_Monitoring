"""
WebSocket endpoints для энергетических устройств.
Интегрированы в основное приложение.
"""
import asyncio
import ast
import json
import os
import socket
from datetime import datetime
from typing import Optional
from uuid import uuid4
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger

from backend.database.db import get_db, async_session_maker
from backend.database.models import User, EnergeticDevice
from backend.repository.energy import upsert_energetic_device
from backend.repository.energy.power_measurements import (
    persist_deye_modbus_power_measurement_from_ws,
    persist_pi30_power_measurement_from_ws,
)
from backend.repository.energy.energy_meter_measurements import persist_tac4300ct_measurement_from_ws
from backend.services.shared.websocket_events_manager import websocket_events_manager
from backend.database.redis_db import redis_client
from backend.services.energy.modbus_cache import (
    get_cor_agent_snapshot_cache,
    get_polling_snapshot_cache,
    get_modbus_cache,
    get_pi30_parsed_cache,
    set_pi30_parsed_cache,
    set_cor_agent_snapshot_cache,
)
from backend.services.energy.pi30_parser import parse_pi30_event
from backend.services.shared.websocket_auth import extract_websocket_bearer_token
from backend.services.user.auth import auth_service
from backend.routes.devices.websocket_routes import broadcast_manager
from passlib.context import CryptContext


router = APIRouter()

POLLING_WS_PUSH_INTERVAL_SECONDS = 1.0
POLLING_WS_MAX_AGE_SECONDS = 90
POLLING_WS_PING_INTERVAL_SECONDS = 60
POLLING_WS_SEND_TIMEOUT_SECONDS = 1.0
POLLING_WS_RAW_CACHE_SCAN_INTERVAL_SECONDS = 5
DEVICE_STATUS_WS_PUSH_INTERVAL_SECONDS = 1.0
DEVICE_STATUS_SETTINGS_REQUEST_COOLDOWN_SECONDS = 45
ENABLE_PI30_CACHE_ENRICHMENT = True
ENABLE_PI30_WS_RESPONSE_ENRICHMENT = os.getenv("ENABLE_PI30_WS_RESPONSE_ENRICHMENT", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# Инициализация контекста для проверки паролей
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Проверяет соответствие plain-text пароля хешированному.
    """
    return pwd_context.verify(plain_password, hashed_password)


async def _authenticate_frontend_websocket(
    websocket: WebSocket,
    db: AsyncSession,
) -> User | None:
    token = extract_websocket_bearer_token(websocket)
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return None

    try:
        return await auth_service.get_current_user(token=token, db=db)
    except HTTPException as exc:
        logger.warning(f"Rejected frontend WebSocket connection: {exc.detail}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return None


async def get_user_by_email(email: str, db: AsyncSession):
    """
    Получает пользователя по email из базы данных.
    """
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


def _snapshot_marker(snapshot: dict) -> str:
    """Builds a stable marker to avoid resending the same polling snapshot."""
    return f"{snapshot.get('cached_at')}:{snapshot.get('measured_at')}"


async def _get_registered_bridge_ids() -> list[str]:
    async with async_session_maker() as db:
        result = await db.execute(select(EnergeticDevice.id, EnergeticDevice.device_id))
        rows = result.all()

    bridge_ids: list[str] = []
    for _, device_id in rows:
        if device_id:
            bridge_ids.append(str(device_id))
    return sorted(set(bridge_ids))


async def _load_device_id_aliases() -> tuple[dict[str, str], dict[str, str]]:
    async with async_session_maker() as db:
        result = await db.execute(select(EnergeticDevice.id, EnergeticDevice.device_id))
        rows = result.all()

    by_db_id: dict[str, str] = {}
    by_device_id: dict[str, str] = {}
    for db_id, device_id in rows:
        if not device_id:
            continue
        canonical = str(device_id)
        by_device_id[canonical] = canonical
        if db_id:
            by_db_id[str(db_id)] = canonical

    return by_db_id, by_device_id


def _canonicalize_alias(alias: Optional[str], by_db_id: dict[str, str], by_device_id: dict[str, str]) -> Optional[str]:
    if not alias:
        return None
    alias_s = str(alias)
    return by_db_id.get(alias_s) or by_device_id.get(alias_s) or alias_s


async def _get_connected_bridge_aliases() -> set[str]:
    aliases: set[str] = set()
    connection_ids = await redis_client.smembers("ws:connections")
    for connection_id in connection_ids:
        conn_data = await redis_client.hgetall(f"ws:connection:{connection_id}")
        if not conn_data:
            continue
        for candidate in (conn_data.get("session_id"), conn_data.get("device_id"), conn_data.get("energetic_device_id")):
            if candidate:
                aliases.add(str(candidate))
    return aliases


def _extract_session_id_from_running_payload(raw_value: Optional[str]) -> Optional[str]:
    if not raw_value:
        return None
    try:
        parsed = ast.literal_eval(str(raw_value))
    except (ValueError, SyntaxError):
        return None

    if not isinstance(parsed, dict):
        return None

    session_id = parsed.get("session_id")
    return str(session_id) if session_id else None


async def _get_running_polling_session_aliases() -> set[str]:
    aliases: set[str] = set()
    async for key in redis_client.scan_iter(match="ws:broadcast:task:*:running", count=200):
        raw_value = await redis_client.get(key)
        session_id = _extract_session_id_from_running_payload(raw_value)
        if session_id:
            aliases.add(session_id)
    return aliases


def _settings_request_throttle_key(device_id: str) -> str:
    return f"ws:status:settings-request:{device_id}"


async def _request_system_settings_for_online_devices(device_ids: set[str]) -> None:
    """Requests status settings once per cooldown window for each online device."""
    if not device_ids:
        return

    for device_id in sorted(device_ids):
        throttle_key = _settings_request_throttle_key(device_id)
        try:
            allowed = await redis_client.set(
                throttle_key,
                "1",
                ex=DEVICE_STATUS_SETTINGS_REQUEST_COOLDOWN_SECONDS,
                nx=True,
            )
        except Exception:
            allowed = True

        if not allowed:
            continue

        sent = await websocket_events_manager.send_to_session(
            session_id=device_id,
            event_data={
                "command_type": "get_settings",
                "settings_requested": ["test"],
            },
        )

        if not sent:
            try:
                await redis_client.delete(throttle_key)
            except Exception:
                pass


def _extract_system_settings_from_snapshot(snapshot: Optional[dict]) -> Optional[dict]:
    if not isinstance(snapshot, dict):
        return None

    events = snapshot.get("events")
    if not isinstance(events, dict) or not events:
        return None

    candidates = sorted(
        [event for event in events.values() if isinstance(event, dict)],
        key=lambda item: str(item.get("timestamp") or ""),
        reverse=True,
    )

    for event in candidates:
        message = event.get("data")
        if not isinstance(message, dict):
            continue

        if message.get("command_type") != "settings_response":
            continue
        if str(message.get("category") or "").lower() not in {"system", "test"}:
            continue

        system_data = message.get("data")
        if not isinstance(system_data, dict) or not any(
            key in system_data
            for key in (
                "build_number", "build_date", "refresh_interval", "log_level",
                "debug_mode", "ws_server", "node_name",
            )
        ):
            continue

        return {
            "build_number": system_data.get("build_number"),
            "build_date": system_data.get("build_date"),
            "node_name": system_data.get("node_name"),
            "refresh_interval": system_data.get("refresh_interval"),
            "log_level": system_data.get("log_level"),
            "debug_mode": system_data.get("debug_mode"),
            "ws_server": system_data.get("ws_server"),
            "settings_timestamp": event.get("timestamp"),
        }

    return None


async def _build_cor_bridge_status_snapshot() -> dict:
    by_db_id, by_device_id = await _load_device_id_aliases()
    registered_bridge_ids = await _get_registered_bridge_ids()

    connected_aliases = await _get_connected_bridge_aliases()
    connected_ids = {
        canonical
        for alias in connected_aliases
        if (canonical := _canonicalize_alias(alias, by_db_id, by_device_id))
    }

    running_polling_aliases = await _get_running_polling_session_aliases()
    polling_ids = {
        canonical
        for alias in running_polling_aliases
        if (canonical := _canonicalize_alias(alias, by_db_id, by_device_id))
    }
    snapshots = await asyncio.gather(
        *(
            get_cor_agent_snapshot_cache(
                device_id=device_id,
                max_age_seconds=POLLING_WS_MAX_AGE_SECONDS,
            )
            for device_id in registered_bridge_ids
        )
    )
    snapshot_by_device_id = {
        device_id: snapshot
        for device_id, snapshot in zip(registered_bridge_ids, snapshots)
    }
    recent_response_ids = {
        device_id
        for device_id, snapshot in snapshot_by_device_id.items()
        if isinstance(snapshot, dict) and isinstance(snapshot.get("events"), dict) and bool(snapshot.get("events"))
    }

    now_iso = datetime.utcnow().isoformat()

    devices = []
    for device_id in registered_bridge_ids:
        is_online = device_id in connected_ids
        has_background_polling = device_id in polling_ids
        has_recent_response = device_id in recent_response_ids
        system_settings = _extract_system_settings_from_snapshot(snapshot_by_device_id.get(device_id))

        if not is_online:
            status_name = "inactive"
        elif has_background_polling and has_recent_response:
            status_name = "active"
        else:
            status_name = "stopped"

        devices.append(
            {
                "device_id": device_id,
                "status": status_name,
                "is_online": is_online,
                "has_background_polling": has_background_polling,
                "has_recent_response": has_recent_response,
                "system_settings": system_settings,
                "updated_at": now_iso,
            }
        )

    marker = json.dumps(
        [
            (
                item["device_id"],
                item["status"],
                (item.get("system_settings") or {}).get("build_number"),
                (item.get("system_settings") or {}).get("build_date"),
                (item.get("system_settings") or {}).get("settings_timestamp"),
            )
            for item in devices
        ],
        ensure_ascii=True,
        sort_keys=True,
    )

    return {
        "devices": devices,
        "count": len(devices),
        "timestamp": now_iso,
        "marker": marker,
    }

async def _push_cor_agent_snapshot_if_available(
    websocket: WebSocket,
    device_id: Optional[str],
) -> None:
    if not device_id:
        return

    snapshot = await get_cor_agent_snapshot_cache(
        device_id=device_id,
        max_age_seconds=POLLING_WS_MAX_AGE_SECONDS,
    )
    if not snapshot:
        return

    events = snapshot.get("events", {})
    if not isinstance(events, dict) or not events:
        return

    preferred_order = {
        "grid": 0,
        "batt": 1,
        "battery": 1,
        "pv_1_4": 2,
        "pv1-4": 2,
        "pv_5_8": 3,
        "pv5-8": 3,
        "load": 4,
        "service": 5,
        "settings": 6,
    }
    ordered_events = sorted(
        events.values(),
        key=lambda item: preferred_order.get(
            str(item.get("data", {}).get("command_name") or item.get("command_name") or "").lower(),
            100,
        ),
    )

    await asyncio.wait_for(
        websocket.send_json(
            {
                "type": "cor_agent_snapshot",
                "device_id": device_id,
                "events": ordered_events,
                "cached_at": snapshot.get("cached_at"),
                "source": "cache",
                "timestamp": datetime.utcnow().isoformat(),
            }
        ),
        timeout=POLLING_WS_SEND_TIMEOUT_SECONDS,
    )


async def _push_polling_snapshot_if_needed(
    websocket: WebSocket,
    connection_id: str,
    last_snapshot_marker: str,
    last_channel_marker: str,
    last_raw_cache_marker: str,
    allow_raw_cache_scan: bool,
) -> tuple[str, str, str]:
    subscriber = websocket_events_manager.frontend_subscribers.get(connection_id)
    if not subscriber:
        return last_snapshot_marker, last_channel_marker, last_raw_cache_marker

    subscribed_device_id = subscriber.get("device_id")
    if not subscribed_device_id:
        return last_snapshot_marker, last_channel_marker, last_raw_cache_marker

    snapshot = await get_polling_snapshot_cache(
        object_id=subscribed_device_id,
        max_age_seconds=POLLING_WS_MAX_AGE_SECONDS,
    )
    if not snapshot:
        if not allow_raw_cache_scan:
            return last_snapshot_marker, last_channel_marker, last_raw_cache_marker

        raw_event = await _get_latest_raw_cache_event_for_object(
            object_id=subscribed_device_id,
            max_age_seconds=POLLING_WS_MAX_AGE_SECONDS,
        )
        if not raw_event:
            return last_snapshot_marker, last_channel_marker, last_raw_cache_marker

        raw_marker = raw_event.get("marker")
        if raw_marker and raw_marker != last_raw_cache_marker:
            channel_data = raw_event.get("data", [])
            v1_cached_response = {
                "ok": True,
                "data": channel_data,
                "source": "cache",
                "cached_at": raw_event.get("cached_at"),
            }
            await asyncio.wait_for(
                websocket.send_json(
                    {
                        "type": "polling_cache_channel",
                        "device_id": subscribed_device_id,
                        "channel": raw_event.get("channel"),
                        "endpoint": raw_event.get("endpoint"),
                        "group": raw_event.get("group"),
                        "func_code": raw_event.get("func_code"),
                        "ok": True,
                        "data": channel_data,
                        "meta": raw_event.get("meta", {}),
                        "request": raw_event.get("request", {}),
                        "v1_cached_response": v1_cached_response,
                        "coils": channel_data if raw_event.get("channel") == "read_coils" else None,
                        "inputs": channel_data if raw_event.get("channel") == "read_discrete_inputs" else None,
                        "source": "cache",
                        "timestamp": raw_event.get("measured_at") or datetime.utcnow().isoformat(),
                        "fallback": "raw_cache_by_object_id",
                    }
                ),
                timeout=POLLING_WS_SEND_TIMEOUT_SECONDS,
            )
            last_raw_cache_marker = raw_marker

        return last_snapshot_marker, last_channel_marker, last_raw_cache_marker

    channel_event = snapshot.get("channel_event")
    if isinstance(channel_event, dict):
        channel_measured_at = channel_event.get("measured_at")
        channel_marker = (
            f"{snapshot.get('cached_at')}:{channel_event.get('channel')}:{channel_event.get('group')}:{channel_measured_at}"
        )
        if channel_marker != last_channel_marker:
            channel_data = channel_event.get("data", [])
            v1_cached_response = {
                "ok": channel_event.get("ok", True),
                "data": channel_data,
                "source": channel_event.get("source", "cache"),
                "cached_at": snapshot.get("cached_at"),
            }
            await asyncio.wait_for(
                websocket.send_json(
                    {
                        "type": "polling_cache_channel",
                        "device_id": subscribed_device_id,
                        "channel": channel_event.get("channel"),
                        "endpoint": channel_event.get("endpoint"),
                        "group": channel_event.get("group"),
                        "func_code": channel_event.get("func_code"),
                        "ok": v1_cached_response["ok"],
                        "data": channel_data,
                        "meta": channel_event.get("meta", {}),
                        "request": channel_event.get("request", {}),
                        "v1_cached_response": v1_cached_response,
                        "coils": channel_data if channel_event.get("channel") == "read_coils" else None,
                        "inputs": channel_data if channel_event.get("channel") == "read_discrete_inputs" else None,
                        "source": channel_event.get("source", "cache"),
                        "timestamp": channel_measured_at or datetime.utcnow().isoformat(),
                    }
                ),
                timeout=POLLING_WS_SEND_TIMEOUT_SECONDS,
            )
            last_channel_marker = channel_marker

    marker = _snapshot_marker(snapshot)
    if marker == last_snapshot_marker:
        return last_snapshot_marker, last_channel_marker, last_raw_cache_marker

    await asyncio.wait_for(
        websocket.send_json(
            {
                "type": "polling_snapshot",
                "device_id": subscribed_device_id,
                "ok": snapshot.get("ok", True),
                "data": snapshot.get("data", {}),
                "cached_at": snapshot.get("cached_at"),
                "measured_at": snapshot.get("measured_at"),
                "timestamp": snapshot.get("measured_at") or datetime.utcnow().isoformat(),
                "source": "cache",
                "cache_route": "/v1_cached",
            }
        ),
        timeout=POLLING_WS_SEND_TIMEOUT_SECONDS,
    )
    return marker, last_channel_marker, last_raw_cache_marker


def _parse_raw_cache_key(key: str, object_id: str):
    parts = key.split(":")
    if len(parts) < 10:
        return None

    cache_type = parts[2]
    protocol = parts[3]
    host = parts[4]
    port = int(parts[5])
    key_object_id = parts[6]
    slave_id = int(parts[7])
    start = int(parts[8])
    count = int(parts[9])

    if key_object_id != object_id:
        return None

    if cache_type == "registers":
        if len(parts) < 11:
            return None
        func_code = int(parts[10])
        channel = "read"
        endpoint = "/v1_cached/read"
    elif cache_type == "coils":
        func_code = 1
        channel = "read_coils"
        endpoint = "/v1_cached/read_coils"
    elif cache_type == "discrete_inputs":
        func_code = 2
        channel = "read_discrete_inputs"
        endpoint = "/v1_cached/read_discrete_inputs"
    else:
        return None

    return {
        "cache_type": cache_type,
        "protocol": protocol,
        "host": host,
        "port": port,
        "object_id": key_object_id,
        "slave_id": slave_id,
        "start": start,
        "count": count,
        "func_code": func_code,
        "channel": channel,
        "endpoint": endpoint,
    }


async def _store_pi30_parsed_cache_if_enabled(device_id: str, event_data: dict) -> None:
    """Best-effort PI30 enrichment stored separately from snapshot/live websocket payloads."""
    if not ENABLE_PI30_CACHE_ENRICHMENT:
        return

    try:
        parsed_pi30 = parse_pi30_event(event_data.get("data") or {})
    except Exception as exc:
        logger.warning(f"Failed to parse PI30 cache event: {exc}")
        return

    if parsed_pi30 is None:
        return

    try:
        await set_pi30_parsed_cache(device_id=device_id, parsed_event=parsed_pi30)
    except Exception as exc:
        logger.warning(f"Failed to store PI30 parsed cache for {device_id}: {exc}")


def _with_parsed_pi30_if_enabled(message: dict) -> dict:
    if not ENABLE_PI30_WS_RESPONSE_ENRICHMENT or not isinstance(message, dict):
        return message

    if "parsed_pi30" in message:
        return message

    try:
        parsed = parse_pi30_event(message)
    except Exception as exc:
        logger.warning(f"Failed to build parsed_pi30 for websocket response: {exc}")
        return message

    if parsed is None:
        return message

    enriched = dict(message)
    enriched["parsed_pi30"] = parsed
    return enriched


def _extract_parsed_pi30_from_snapshot(snapshot: dict) -> Optional[dict]:
    events = snapshot.get("events", {}) if isinstance(snapshot, dict) else {}
    if not isinstance(events, dict) or not events:
        return None

    # Prefer latest event by timestamp when possible.
    ordered_events = sorted(
        events.values(),
        key=lambda item: str(item.get("timestamp") or ""),
        reverse=True,
    )

    for event in ordered_events:
        if not isinstance(event, dict):
            continue
        try:
            parsed = parse_pi30_event(event.get("data") or {})
        except Exception as exc:
            logger.warning(f"Failed to parse PI30 from snapshot event: {exc}")
            continue
        if parsed is not None:
            return parsed

    return None


async def _recover_pi30_parsed_cache_from_snapshot(
    device_id: str,
    max_age_seconds: Optional[int],
) -> Optional[dict]:
    snapshot = await get_cor_agent_snapshot_cache(
        device_id=device_id,
        max_age_seconds=max_age_seconds,
    )
    parsed = _extract_parsed_pi30_from_snapshot(snapshot or {})
    if parsed is not None:
        await set_pi30_parsed_cache(device_id=device_id, parsed_event=parsed)
        return await get_pi30_parsed_cache(device_id=device_id, max_age_seconds=max_age_seconds)

    # Fallback: scan all snapshot keys and find events that belong to requested device_id.
    pattern = "modbus:cache:cor_agent_snapshot:*"
    best_match_payload = None
    best_cached_at = 0

    async for key in redis_client.scan_iter(match=pattern, count=100):
        payload = await get_modbus_cache(key, max_age_seconds=max_age_seconds)
        if not payload:
            continue

        events = payload.get("events", {})
        if not isinstance(events, dict) or not events:
            continue

        key_suffix = str(key).rsplit(":", 1)[-1]
        payload_device_id = str(payload.get("device_id") or "")

        matched_events = {}
        for event_key, event_value in events.items():
            if not isinstance(event_value, dict):
                continue

            event_device_id = str(event_value.get("device_id") or "")
            nested_event_device_id = str((event_value.get("data") or {}).get("device_id") or "")

            if (
                event_device_id == str(device_id)
                or nested_event_device_id == str(device_id)
                or payload_device_id == str(device_id)
                or key_suffix == str(device_id)
            ):
                matched_events[event_key] = event_value

        matched_snapshot = {"events": matched_events}
        parsed_from_match = _extract_parsed_pi30_from_snapshot(matched_snapshot)
        if parsed_from_match is None:
            continue

        cached_at = int(payload.get("cached_at", 0) or 0)
        if cached_at >= best_cached_at:
            best_cached_at = cached_at
            best_match_payload = parsed_from_match

    if best_match_payload is None:
        return None

    await set_pi30_parsed_cache(device_id=device_id, parsed_event=best_match_payload)
    return await get_pi30_parsed_cache(device_id=device_id, max_age_seconds=max_age_seconds)


def _event_matches_device(event: dict, device_id: str) -> bool:
    if not isinstance(event, dict):
        return False
    event_device_id = str(event.get("device_id") or "")
    nested_device_id = str((event.get("data") or {}).get("device_id") or "")
    return event_device_id == str(device_id) or nested_device_id == str(device_id)


def _build_event_probe(event_key: str, event: dict, device_id: str) -> dict:
    event_data = event.get("data") if isinstance(event, dict) else {}
    try:
        parsed = parse_pi30_event(event_data or {}) if isinstance(event_data, dict) else None
    except Exception as exc:
        parsed = None
        parse_error = str(exc)
    else:
        parse_error = None

    cmd_candidate = None
    if isinstance(event_data, dict):
        cmd_candidate = (
            event_data.get("cmd")
            or event_data.get("command_type")
            or event_data.get("command_name")
            or event_data.get("pi30_command")
        )

    has_hex_payload = False
    if isinstance(event_data, dict):
        has_hex_payload = any(
            event_data.get(field) is not None
            for field in ("hex_response", "hex_data", "pi30")
        )

    return {
        "event_key": event_key,
        "event_timestamp": event.get("timestamp") if isinstance(event, dict) else None,
        "event_matches_device": _event_matches_device(event, device_id),
        "cmd_candidate": cmd_candidate,
        "has_hex_payload": has_hex_payload,
        "parse_status": parsed.get("status") if parsed else None,
        "parse_cmd": parsed.get("cmd") if parsed else None,
        "parse_error": parse_error,
    }

# ==================== PI30 кеширование =====================
@router.get(
    "/debug/pi30_parsed",
    status_code=status.HTTP_200_OK,
    summary="Получить последний PI30 parsed cache по device_id",
)
async def debug_get_pi30_parsed_cache(
    device_id: str,
    max_age_seconds: int = POLLING_WS_MAX_AGE_SECONDS,
):
    recovery_attempted = False
    stale_fallback_attempted = False

    payload = await get_pi30_parsed_cache(
        device_id=device_id,
        max_age_seconds=max_age_seconds,
    )

    if payload is None and ENABLE_PI30_CACHE_ENRICHMENT:
        recovery_attempted = True
        payload = await _recover_pi30_parsed_cache_from_snapshot(
            device_id=device_id,
            max_age_seconds=max_age_seconds,
        )

    # Fallback for diagnostics: if strict max_age misses, try latest available cached data.
    if payload is None and ENABLE_PI30_CACHE_ENRICHMENT:
        stale_fallback_attempted = True
        payload = await get_pi30_parsed_cache(
            device_id=device_id,
            max_age_seconds=None,
        )

    if payload is None and ENABLE_PI30_CACHE_ENRICHMENT:
        stale_fallback_attempted = True
        payload = await _recover_pi30_parsed_cache_from_snapshot(
            device_id=device_id,
            max_age_seconds=None,
        )

    return {
        "debug_route_revision": "pi30-debug-v4",
        "runtime": {
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "utc_now": datetime.utcnow().isoformat(),
        },
        "enabled": ENABLE_PI30_CACHE_ENRICHMENT,
        "device_id": device_id,
        "max_age_seconds": max_age_seconds,
        "recovery_attempted": recovery_attempted,
        "stale_fallback_attempted": stale_fallback_attempted,
        "cache": payload,
    }


@router.get(
    "/debug/pi30_parsed_sources",
    status_code=status.HTTP_200_OK,
    summary="Диагностика источников PI30 parsed cache по device_id",
)
async def debug_get_pi30_parsed_sources(
    device_id: str,
    max_age_seconds: int = POLLING_WS_MAX_AGE_SECONDS,
    scan_limit: int = 200,
):
    safe_scan_limit = max(10, min(int(scan_limit), 1000))

    strict_pi30_cache = await get_pi30_parsed_cache(
        device_id=device_id,
        max_age_seconds=max_age_seconds,
    )
    stale_pi30_cache = await get_pi30_parsed_cache(
        device_id=device_id,
        max_age_seconds=None,
    )

    direct_snapshot = await get_cor_agent_snapshot_cache(
        device_id=device_id,
        max_age_seconds=max_age_seconds,
    )

    scanned = 0
    candidates = []
    pattern = "modbus:cache:cor_agent_snapshot:*"
    async for key in redis_client.scan_iter(match=pattern, count=100):
        scanned += 1
        if scanned > safe_scan_limit:
            break

        payload = await get_modbus_cache(key, max_age_seconds=None)
        if not payload:
            continue

        events = payload.get("events", {})
        if not isinstance(events, dict):
            events = {}

        key_suffix = str(key).rsplit(":", 1)[-1]
        payload_device_id = str(payload.get("device_id") or "")
        key_matches = key_suffix == str(device_id)
        payload_matches = payload_device_id == str(device_id)

        event_probes = []
        matched_events_count = 0
        for event_key, event_value in events.items():
            if not isinstance(event_value, dict):
                continue
            probe = _build_event_probe(event_key, event_value, device_id)
            event_probes.append(probe)
            if probe["event_matches_device"]:
                matched_events_count += 1

        any_pi30_parsed = any(probe.get("parse_status") for probe in event_probes)

        if key_matches or payload_matches or matched_events_count > 0 or any_pi30_parsed:
            candidates.append(
                {
                    "key": str(key),
                    "key_suffix": key_suffix,
                    "payload_device_id": payload_device_id or None,
                    "key_matches_device": key_matches,
                    "payload_matches_device": payload_matches,
                    "cached_at": payload.get("cached_at"),
                    "events_count": len(events),
                    "matched_events_count": matched_events_count,
                    "pi30_parsed_events_count": len([p for p in event_probes if p.get("parse_status")]),
                    "event_probes": event_probes[:10],
                }
            )

    candidates.sort(key=lambda item: int(item.get("cached_at") or 0), reverse=True)

    return {
        "debug_route_revision": "pi30-debug-v4",
        "runtime": {
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "utc_now": datetime.utcnow().isoformat(),
        },
        "device_id": device_id,
        "max_age_seconds": max_age_seconds,
        "scan_limit": safe_scan_limit,
        "strict_pi30_cache_exists": strict_pi30_cache is not None,
        "stale_pi30_cache_exists": stale_pi30_cache is not None,
        "direct_snapshot_exists": direct_snapshot is not None,
        "scanned_keys": scanned,
        "candidate_keys": candidates[:20],
    }
# ==================== PI30 кеширование =====================

async def _get_latest_raw_cache_event_for_object(object_id: str, max_age_seconds: int):
    patterns = [
        f"modbus:cache:registers:*:*:*:{object_id}:*:*:*:*",
        f"modbus:cache:coils:*:*:*:{object_id}:*:*:*",
        f"modbus:cache:discrete_inputs:*:*:*:{object_id}:*:*:*",
    ]

    best_event = None
    best_cached_at = 0

    for pattern in patterns:
        async for key in redis_client.scan_iter(match=pattern, count=100):
            parsed = _parse_raw_cache_key(key, object_id)
            if not parsed:
                continue

            payload = await get_modbus_cache(key, max_age_seconds=max_age_seconds)
            if not payload:
                continue

            cached_at = int(payload.get("cached_at", 0) or 0)
            if cached_at < best_cached_at:
                continue

            best_cached_at = cached_at
            best_event = {
                "marker": f"{key}:{cached_at}",
                "channel": parsed["channel"],
                "endpoint": parsed["endpoint"],
                "group": None,
                "func_code": parsed["func_code"],
                "data": payload.get("data", []),
                "cached_at": cached_at,
                "measured_at": payload.get("measured_at"),
                "meta": {
                    "start": parsed["start"],
                    "count": parsed["count"],
                    "slave_id": parsed["slave_id"],
                },
                "request": {
                    "protocol": parsed["protocol"],
                    "host": parsed["host"],
                    "port": parsed["port"],
                    "slave_id": parsed["slave_id"],
                    "object_id": parsed["object_id"],
                    "start": parsed["start"],
                    "count": parsed["count"],
                    "func_code": parsed["func_code"],
                },
            }

    return best_event


@router.websocket("/wssresponses")
async def websocket_device_responses(
    websocket: WebSocket,
    device_id: str = None,
    db: AsyncSession = Depends(get_db),
):
    """
    WebSocket endpoint для фронтенда, получает ответы от энергетических устройств.
    
    **Использование:**
    - `/wssresponses?device_id=COR-B0B21CA3435C` - только ответы от конкретного устройства
    - `/wssresponses` - ответы от всех подключенных устройств
    
    **Формат данных:**
    ```json
    {
        "device_id": "COR-B0B21CA3435C",
        "data": {...},
        "timestamp": "2026-01-02T09:18:41.123456"
    }
    ```
    """
    logger.info(f"🔌 WebSocket connection attempt to /wssresponses with device_id={device_id}")
    current_user = await _authenticate_frontend_websocket(websocket, db)
    if not current_user:
        return

    await websocket.accept()
    connection_id = str(uuid4())
    
    logger.info(f"Frontend client connected to device responses (device_id={device_id}, connection_id={connection_id})")
    
    try:
        # Добавляем клиента в список подписчиков
        websocket_events_manager.frontend_subscribers[connection_id] = {
            "websocket": websocket,
            "device_id": device_id,  # None = все устройства, иначе фильтруем по device_id
            "connected_at": datetime.utcnow()
        }
        
        # Отправляем подтверждение подключения
        await websocket.send_json({
            "type": "connection_established",
            "connection_id": connection_id,
            "device_id": device_id,
            "timestamp": datetime.utcnow().isoformat()
        })
        await _push_cor_agent_snapshot_if_available(websocket, device_id)

        last_snapshot_marker = ""
        last_channel_marker = ""
        last_raw_cache_marker = ""
        last_ping_sent_at = datetime.utcnow()
        last_raw_cache_scan_at = datetime.utcnow()
        
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=POLLING_WS_PUSH_INTERVAL_SECONDS,
                )
                try:
                    msg = json.loads(data) if isinstance(data, str) else data
                    if msg.get("action") == "change_device_id":
                        new_device_id = msg.get("device_id")
                        websocket_events_manager.frontend_subscribers[connection_id]["device_id"] = new_device_id
                        last_snapshot_marker = ""
                        last_channel_marker = ""
                        await websocket.send_json({
                            "type": "subscription_changed",
                            "device_id": new_device_id,
                            "timestamp": datetime.utcnow().isoformat()
                        })
                        await _push_cor_agent_snapshot_if_available(websocket, new_device_id)
                        logger.info(f"Frontend client {connection_id} changed subscription to device_id={new_device_id}")
                except (json.JSONDecodeError, ValueError):
                    pass
            except asyncio.TimeoutError:
                try:
                    now = datetime.utcnow()
                    allow_raw_cache_scan = (
                        (now - last_raw_cache_scan_at).total_seconds()
                        >= POLLING_WS_RAW_CACHE_SCAN_INTERVAL_SECONDS
                    )
                    last_snapshot_marker, last_channel_marker, last_raw_cache_marker = await _push_polling_snapshot_if_needed(
                        websocket=websocket,
                        connection_id=connection_id,
                        last_snapshot_marker=last_snapshot_marker,
                        last_channel_marker=last_channel_marker,
                        last_raw_cache_marker=last_raw_cache_marker,
                        allow_raw_cache_scan=allow_raw_cache_scan,
                    )
                    if allow_raw_cache_scan:
                        last_raw_cache_scan_at = now
                except Exception:
                    break

                try:
                    if (now - last_ping_sent_at).total_seconds() >= POLLING_WS_PING_INTERVAL_SECONDS:
                        await websocket.send_json({
                            "type": "ping",
                            "timestamp": now.isoformat(),
                        })
                        last_ping_sent_at = now
                except:
                    break
    
    except WebSocketDisconnect:
        logger.info(f"Frontend client {connection_id} disconnected normally")
    except Exception as e:
        logger.error(f"Error in frontend device responses connection {connection_id}: {e}", exc_info=True)
    finally:
        # Удаляем клиента из подписчиков
        websocket_events_manager.frontend_subscribers.pop(connection_id, None)
        logger.info(f"Frontend client {connection_id} disconnected")


@router.websocket("/wssstatuses")
@router.websocket("/dev-modbus/statuses")
async def websocket_cor_bridge_statuses(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
):
    """
    Канал статусов COR Bridge в реальном времени.

    Статусы:
    - active: устройство online и на нем есть фоновые polling-задачи
    - stopped: устройство online, но фоновых polling-задач нет (только keepalive)
    - inactive: устройство offline (соединение отсутствует)
    """
    current_user = await _authenticate_frontend_websocket(websocket, db)
    if not current_user:
        return

    await websocket.accept()
    connection_id = str(uuid4())

    logger.info(f"Frontend client connected to bridge statuses (connection_id={connection_id})")

    try:
        await websocket.send_json(
            {
                "type": "bridge_statuses_connection_established",
                "connection_id": connection_id,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

        initial_snapshot = await _build_cor_bridge_status_snapshot()
        await _request_system_settings_for_online_devices(
            {
                item["device_id"]
                for item in initial_snapshot["devices"]
                if item.get("is_online")
            }
        )

        last_ping_sent_at = datetime.utcnow()

        while True:
            try:
                incoming = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=DEVICE_STATUS_WS_PUSH_INTERVAL_SECONDS,
                )
                if incoming == "ping":
                    await websocket.send_text("pong")
                    continue

                try:
                    message = json.loads(incoming) if isinstance(incoming, str) else incoming
                except (json.JSONDecodeError, ValueError):
                    message = None

                if isinstance(message, dict) and message.get("action") in {"refresh", "get_statuses"}:
                    await _request_system_settings_for_online_devices(
                        {
                            item["device_id"]
                            for item in (await _build_cor_bridge_status_snapshot())["devices"]
                            if item.get("is_online")
                        }
                    )
            except asyncio.TimeoutError:
                pass

            snapshot = await _build_cor_bridge_status_snapshot()
            await _request_system_settings_for_online_devices(
                {
                    item["device_id"]
                    for item in snapshot["devices"]
                    if item.get("is_online") and item.get("system_settings") is None
                }
            )
            await asyncio.wait_for(
                websocket.send_json(
                    {
                        "type": "cor_bridge_statuses",
                        "statuses": snapshot["devices"],
                        "count": snapshot["count"],
                        "timestamp": snapshot["timestamp"],
                    }
                ),
                timeout=POLLING_WS_SEND_TIMEOUT_SECONDS,
            )

            now = datetime.utcnow()
            if (now - last_ping_sent_at).total_seconds() >= POLLING_WS_PING_INTERVAL_SECONDS:
                await websocket.send_json(
                    {
                        "type": "ping",
                        "timestamp": now.isoformat(),
                    }
                )
                last_ping_sent_at = now

    except WebSocketDisconnect:
        logger.info(f"Bridge statuses client {connection_id} disconnected normally")
    except Exception as e:
        logger.error(f"Error in bridge statuses websocket {connection_id}: {e}", exc_info=True)


@router.websocket("/wssdevices")
async def websocket_energetic_device_endpoint(
    websocket: WebSocket, 
    session_id: str, 
    db: AsyncSession = Depends(get_db)
):
    """
    WebSocket endpoint для подключения энергетических устройств (Cerbo/Modbus).
    Аутентификация по email/password через первое сообщение.
    Использует websocket_events_manager для broadcast событий.
    """
    logger.info(f"🔌 WebSocket connection attempt to /wssdevices with session_id={session_id}")
    # Сначала принимаем WebSocket соединение
    await websocket.accept()
    
    connection_id = await websocket_events_manager.connect(websocket, session_id=session_id, accept_connection=False)
    
    user = None
    device_name = None
    device_protocol = None
    device_description = None
    device_hardware_id = None

    try:
        # Первое сообщение с credentials
        auth_data = await websocket.receive_json()
        user_email = auth_data.get("email")
        password = auth_data.get("password")
        device_name = auth_data.get("node_name") or auth_data.get("device_name") or auth_data.get("name")
        device_protocol = auth_data.get("protocol")
        device_description = auth_data.get("description")

        # Определяем итоговый session_id
        resolved_session_id = session_id or auth_data.get("session_token") or auth_data.get("device_id")
        if not resolved_session_id:
            resolved_session_id = str(uuid4())

        if resolved_session_id != session_id or not websocket_events_manager.active_connections.get(connection_id, {}).get("session_id"):
            previous_session_id = websocket_events_manager.active_connections[connection_id].get("session_id")
            session_id = resolved_session_id
            websocket_events_manager.active_connections[connection_id]["session_id"] = session_id
            if previous_session_id and previous_session_id != session_id:
                await redis_client.delete(f"ws:session:{previous_session_id}")
            await redis_client.hset(f"ws:connection:{connection_id}", mapping={"session_id": session_id})
            await redis_client.set(f"ws:session:{session_id}", connection_id)
        else:
            session_id = resolved_session_id

        device_hardware_id = auth_data.get("device_id") or session_id
        
        if not user_email or not password:
            await websocket.send_json({"cloud_status": "Auth error: Missing credentials"})
            await websocket_events_manager.disconnect(connection_id)
            logger.warning(f"Missing credentials for session {session_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing credentials"
            )
        
        # Проверяем пользователя
        user = await get_user_by_email(email=user_email, db=db)
        if user is None or not verify_password(plain_password=password, hashed_password=user.password):
            await websocket.send_json({"cloud_status": "Auth error: Invalid credentials"})
            await websocket_events_manager.disconnect(connection_id)
            logger.warning(f"Invalid credentials for session {session_id}, email: {user_email}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )
        
        logger.info(f"Energetic device authenticated: session_id={session_id}, user={user_email}")
        await websocket.send_json({
            "command_type": "auth_response",
            "cloud_status": "authenticated",
            "session_token": session_id,
        })
        logger.info(f"Auth response sent to energetic device {session_id}")

        # Регистрируем/обновляем устройство в БД
        energetic_device = await upsert_energetic_device(
            db=db,
            user_id=user.id,
            device_id=device_hardware_id,
            device_name=device_name,
            protocol=device_protocol,
            description=device_description,
        )

        logger.info(f"Energetic device registered: device_id={device_hardware_id}, name={device_name}")

        # Основной цикл получения сообщений от устройства
        while True:
            try:
                raw_message = await websocket.receive_text()
                message = json.loads(raw_message) if isinstance(raw_message, str) else raw_message
                outbound_message = _with_parsed_pi30_if_enabled(message)

                if broadcast_manager.is_good_device_response(message):
                    for alias in {session_id, device_hardware_id}:
                        if alias:
                            broadcast_manager.mark_session_activity(alias)
                else:
                    for alias in {session_id, device_hardware_id}:
                        if alias:
                            broadcast_manager.mark_session_bad_response(alias)
                
                # Логируем команды от устройства
                if message.get("command"):
                    pass
                    # logger.debug(f"Device {device_hardware_id} sent command: {message.get('command')}")
                
                event_payload = {
                    "device_id": device_hardware_id,
                    "data": message,
                    "timestamp": datetime.utcnow().isoformat(),
                }
                for alias in {session_id, device_hardware_id}:
                    if alias:
                        await set_cor_agent_snapshot_cache(alias, event_payload)
                        await _store_pi30_parsed_cache_if_enabled(alias, event_payload)

                await persist_pi30_power_measurement_from_ws(
                    device_aliases={session_id, device_hardware_id},
                    raw_event=message,
                    measured_at=datetime.utcnow(),
                )
                await persist_deye_modbus_power_measurement_from_ws(
                    device_aliases={session_id, device_hardware_id},
                    raw_event=message,
                    measured_at=datetime.utcnow(),
                )
                await persist_tac4300ct_measurement_from_ws(
                    device_aliases={session_id, device_hardware_id},
                    raw_event=message,
                    measured_at=datetime.utcnow(),
                )

                # Broadcast message всем frontend подписчикам
                await websocket_events_manager.broadcast_to_frontend(
                    device_id=device_hardware_id,
                    data=outbound_message
                )
                
            except WebSocketDisconnect:
                logger.info(f"Device {device_hardware_id} disconnected (session_id={session_id})")
                break
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON from device {device_hardware_id}: {e}")
                await websocket.send_json({"error": "Invalid JSON format"})
            except Exception as e:
                logger.error(f"Error processing message from device {device_hardware_id}: {e}", exc_info=True)
                break
    
    except WebSocketDisconnect:
        logger.info(f"Device disconnected during auth (session_id={session_id})")
    except Exception as e:
        logger.error(f"Error in device WebSocket connection: {e}", exc_info=True)
    finally:
        await websocket_events_manager.disconnect(connection_id)
        logger.info(f"Device connection closed (session_id={session_id}, connection_id={connection_id})")


# ============ NGINX proxy routes ============
# These routes handle requests from NGINX at /dev-modbus/

@router.websocket("/dev-modbus/responses")
async def websocket_dev_modbus_responses(
    websocket: WebSocket,
    device_id: str = None,
    db: AsyncSession = Depends(get_db),
):
    """
    WebSocket endpoint для фронтенда через NGINX proxy path /dev-modbus/responses.
    Алиас для /wssresponses с поддержкой реального пути NGINX.
    
    **Использование:**
    - `/dev-modbus/responses?device_id=COR-B0B21CA3435C` - только ответы от конкретного устройства
    - `/dev-modbus/responses` - ответы от всех подключенных устройств
    """
    logger.info(f"🔌 WebSocket connection attempt to /dev-modbus/responses with device_id={device_id}")
    current_user = await _authenticate_frontend_websocket(websocket, db)
    if not current_user:
        return

    await websocket.accept()
    connection_id = str(uuid4())
    
    logger.info(f"Frontend client connected via /dev-modbus/responses (device_id={device_id}, connection_id={connection_id})")
    
    try:
        # Добавляем клиента в список подписчиков
        websocket_events_manager.frontend_subscribers[connection_id] = {
            "websocket": websocket,
            "device_id": device_id,  # None = все устройства, иначе фильтруем по device_id
            "connected_at": datetime.utcnow()
        }
        
        # Отправляем подтверждение подключения
        await websocket.send_json({
            "type": "connection_established",
            "connection_id": connection_id,
            "device_id": device_id,
            "timestamp": datetime.utcnow().isoformat()
        })
        await _push_cor_agent_snapshot_if_available(websocket, device_id)

        last_snapshot_marker = ""
        last_channel_marker = ""
        last_raw_cache_marker = ""
        last_ping_sent_at = datetime.utcnow()
        last_raw_cache_scan_at = datetime.utcnow()
        
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=POLLING_WS_PUSH_INTERVAL_SECONDS,
                )
                try:
                    message = json.loads(data)
                    if message.get("action") == "change_device_id":
                        new_device_id = message.get("device_id")
                        websocket_events_manager.frontend_subscribers[connection_id]["device_id"] = new_device_id
                        last_snapshot_marker = ""
                        last_channel_marker = ""
                        await websocket.send_json({
                            "type": "subscription_changed",
                            "device_id": new_device_id,
                            "timestamp": datetime.utcnow().isoformat()
                        })
                        await _push_cor_agent_snapshot_if_available(websocket, new_device_id)
                        logger.info(f"[{connection_id}] Client changed subscription to device {new_device_id}")
                except (json.JSONDecodeError, ValueError):
                    pass
            except asyncio.TimeoutError:
                try:
                    now = datetime.utcnow()
                    allow_raw_cache_scan = (
                        (now - last_raw_cache_scan_at).total_seconds()
                        >= POLLING_WS_RAW_CACHE_SCAN_INTERVAL_SECONDS
                    )
                    last_snapshot_marker, last_channel_marker, last_raw_cache_marker = await _push_polling_snapshot_if_needed(
                        websocket=websocket,
                        connection_id=connection_id,
                        last_snapshot_marker=last_snapshot_marker,
                        last_channel_marker=last_channel_marker,
                        last_raw_cache_marker=last_raw_cache_marker,
                        allow_raw_cache_scan=allow_raw_cache_scan,
                    )
                    if allow_raw_cache_scan:
                        last_raw_cache_scan_at = now
                except Exception:
                    break

                try:
                    if (now - last_ping_sent_at).total_seconds() >= POLLING_WS_PING_INTERVAL_SECONDS:
                        await websocket.send_json({
                            "type": "ping",
                            "timestamp": now.isoformat(),
                        })
                        last_ping_sent_at = now
                except:
                    break
    
    except WebSocketDisconnect:
        logger.info(f"Frontend client {connection_id} disconnected via /dev-modbus/responses")
    except Exception as e:
        logger.error(f"Error in /dev-modbus/responses connection {connection_id}: {e}", exc_info=True)
    finally:
        websocket_events_manager.frontend_subscribers.pop(connection_id, None)
        logger.info(f"Frontend connection closed via /dev-modbus/responses (connection_id={connection_id})")


@router.websocket("/dev-modbus/devices")
async def websocket_dev_modbus_devices(
    websocket: WebSocket,
    session_id: str = None,
    db: AsyncSession = Depends(get_db)
):
    """
    WebSocket endpoint для подключения устройств (Cerbo/Modbus) через NGINX proxy path /dev-modbus/devices.
    Алиас для /wssdevices с поддержкой реального пути NGINX.
    
    **Использование:**
    ```
    ws://dev.monitoring.int.com/dev-modbus/devices?session_id=<unique-session-id>
    ```
    """
    logger.info(f"🔌 WebSocket connection attempt to /dev-modbus/devices with session_id={session_id}")
    await websocket.accept()
    
    connection_id = await websocket_events_manager.connect(websocket, session_id=session_id, accept_connection=False)
    
    user = None
    device_name = None
    device_protocol = None
    device_description = None
    device_hardware_id = None

    try:
        # Первое сообщение с credentials
        auth_message = await websocket.receive_text()
        auth_data = json.loads(auth_message)
        
        email = auth_data.get("email")
        password = auth_data.get("password")
        device_name = auth_data.get("device_name")
        device_hardware_id = auth_data.get("device_id")
        device_protocol = auth_data.get("protocol", "modbus")
        device_description = auth_data.get("description")
        
        logger.info(f"Device auth via /dev-modbus/devices: {device_hardware_id} ({device_name})")
        
        # Проверяем учетные данные
        user = await get_user_by_email(email, db)
        
        if not user or not verify_password(password, user.hashed_password):
            logger.warning(f"Failed auth via /dev-modbus/devices for {device_hardware_id}: invalid credentials")
            await websocket.send_json({"cloud_status": "authentication_failed"})
            await websocket.close(code=4001, reason="Authentication failed")
            return
        
        # Регистрируем устройство
        await upsert_energetic_device(
            db=db,
            user_id=user.cor_id,
            device_id=device_hardware_id,
            device_name=device_name,
            protocol=device_protocol,
            description=device_description
        )
        
        # Отправляем успешный ответ
        await websocket.send_json({
            "command_type": "auth_response",
            "cloud_status": "authenticated",
            "session_token": session_id,
            "connection_id": connection_id
        })
        logger.info(f"Auth response sent to energetic device {session_id}")
        
        logger.info(f"Device authenticated via /dev-modbus/devices: {device_hardware_id} (user={user.email})")
        
        # Слушаем данные от устройства
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            outbound_message = _with_parsed_pi30_if_enabled(message)

            if broadcast_manager.is_good_device_response(message):
                for alias in {session_id, device_hardware_id}:
                    if alias:
                        broadcast_manager.mark_session_activity(alias)
            else:
                for alias in {session_id, device_hardware_id}:
                    if alias:
                        broadcast_manager.mark_session_bad_response(alias)

            event_payload = {
                "device_id": device_hardware_id,
                "data": message,
                "timestamp": datetime.utcnow().isoformat(),
            }
            for alias in {session_id, device_hardware_id}:
                if alias:
                    await set_cor_agent_snapshot_cache(alias, event_payload)
                    await _store_pi30_parsed_cache_if_enabled(alias, event_payload)

            await persist_pi30_power_measurement_from_ws(
                device_aliases={session_id, device_hardware_id},
                raw_event=message,
                measured_at=datetime.utcnow(),
            )
            await persist_deye_modbus_power_measurement_from_ws(
                device_aliases={session_id, device_hardware_id},
                raw_event=message,
                measured_at=datetime.utcnow(),
            )
            await persist_tac4300ct_measurement_from_ws(
                device_aliases={session_id, device_hardware_id},
                raw_event=message,
                measured_at=datetime.utcnow(),
            )
            
            # Обновляем объект в памяти для быстрого доступа
            websocket_events_manager.connected_devices[device_hardware_id] = {
                "user_id": user.cor_id,
                "connection_id": connection_id,
                "data": outbound_message,
                "timestamp": datetime.utcnow()
            }
            
            # Отправляем подписчикам
            await websocket_events_manager.broadcast_to_device_subscribers(
                device_id=device_hardware_id,
                message={
                    "device_id": device_hardware_id,
                    "data": outbound_message,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
    
    except WebSocketDisconnect:
        logger.info(f"Device disconnected via /dev-modbus/devices (session_id={session_id})")
    except Exception as e:
        logger.error(f"Error in /dev-modbus/devices connection: {e}", exc_info=True)
    finally:
        await websocket_events_manager.disconnect(connection_id)
        logger.info(f"Device connection closed via /dev-modbus/devices (session_id={session_id}, connection_id={connection_id})")
