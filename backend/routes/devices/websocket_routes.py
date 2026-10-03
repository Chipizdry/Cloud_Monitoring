"""
FastAPI приложение для WebSocket соединений с энергетическими устройствами в modbus_worker.
Работает только с Cerbo/Modbus устройствами через email/password аутентификацию.
"""
import asyncio
import ast
import json
import os
import socket
import time
from pathlib import Path
from typing import Optional
from datetime import datetime
from uuid import uuid4
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger

from backend.services.energy.websocket_broadcast import BroadcastTaskManager
from backend.database.db import get_db, async_session_maker
from backend.database.models import User, EnergeticDevice
from backend.repository.energy import upsert_energetic_device
from backend.repository.energy.power_measurements import (
    persist_deye_modbus_power_measurement_from_ws,
    persist_pi30_power_measurement_from_ws,
)
from backend.repository.energy.energy_meter_measurements import persist_tac4300ct_measurement_from_ws
from backend.services.shared.websocket_events_manager import websocket_events_manager
from backend.database.redis_db import check_and_clear_broadcast_reload_signal, redis_client
from backend.services.energy.modbus_cache import (
    get_cor_agent_snapshot_cache,
    get_polling_snapshot_cache,
    get_modbus_cache,
    get_pi30_parsed_cache,
    set_pi30_parsed_cache,
    set_cor_agent_snapshot_cache,
)
from backend.services.energy.pi30_parser import parse_pi30_event
from backend.repository.energy.flywheel_data_collector import try_collect_flywheel_measurement
from passlib.context import CryptContext


# Broadcast manager для управления фоновыми задачами рассылки команд
broadcast_manager = BroadcastTaskManager()

POLLING_WS_PUSH_INTERVAL_SECONDS = 1.0
POLLING_WS_MAX_AGE_SECONDS = 90
POLLING_WS_PING_INTERVAL_SECONDS = 60
POLLING_WS_SEND_TIMEOUT_SECONDS = 1.0
POLLING_WS_RAW_CACHE_SCAN_INTERVAL_SECONDS = 5
DEVICE_STATUS_WS_PUSH_INTERVAL_SECONDS = 1.0
DEVICE_STATUS_SETTINGS_REQUEST_COOLDOWN_SECONDS = 45
DEVICE_WS_COMMAND_GRACE_SECONDS = 5


# БЛОК с опциональной PI30 парсингом и кешированием
ENABLE_PI30_CACHE_ENRICHMENT = True
ENABLE_PI30_WS_RESPONSE_ENRICHMENT = True


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
# ==================== Конец блока PI30 кеширования =====================


def _describe_websocket_close_code(code: Optional[int]) -> str:
    descriptions = {
        1000: "normal closure: peer closed the connection cleanly",
        1001: "going away: peer is leaving/restarting/navigating away",
        1002: "protocol error",
        1003: "unsupported data type",
        1005: "no status code was received",
        1006: "abnormal closure: no close frame was received; TCP/proxy/client dropped the connection",
        1007: "invalid frame payload data",
        1008: "policy violation",
        1009: "message too big",
        1010: "mandatory extension missing",
        1011: "unexpected server error",
        1012: "service restart",
        1013: "try again later / temporary overload",
        1015: "TLS handshake failure",
    }
    if code in descriptions:
        return descriptions[code]
    if code is None:
        return "no close code available"
    if 3000 <= code <= 4999:
        return "application/private close code"
    return "unknown close code"

async def _watch_broadcast_reload_requests():
    last_reload_token = None
    while True:
        await asyncio.sleep(1)
        try:
            reload_requested, last_reload_token = await check_and_clear_broadcast_reload_signal(last_reload_token)
            if reload_requested:
                logger.info("Reloading broadcast tasks after Redis signal")
                await broadcast_manager.reload_from_db()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Failed to process broadcast reload signal: {e}", exc_info=True)


async def _get_registered_bridge_ids() -> list[str]:
    """Returns all known COR Bridge ids from DB."""
    async with async_session_maker() as db:
        result = await db.execute(select(EnergeticDevice.id, EnergeticDevice.device_id))
        rows = result.all()

    bridge_ids: list[str] = []
    for _, device_id in rows:
        if device_id:
            bridge_ids.append(str(device_id))
    return sorted(set(bridge_ids))


async def _load_device_id_aliases() -> tuple[dict[str, str], dict[str, str]]:
    """Builds alias maps: db UUID -> device_id and device_id -> device_id."""
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
    """Returns aliases of currently connected device sockets from Redis."""
    aliases: set[str] = set()
    connection_ids = await redis_client.smembers("ws:connections")
    for connection_id in connection_ids:
        conn_data = await redis_client.hgetall(f"ws:connection:{connection_id}")
        if not conn_data:
            continue

        session_id = conn_data.get("session_id")
        device_id = conn_data.get("device_id")
        energetic_device_id = conn_data.get("energetic_device_id")
        for candidate in (session_id, device_id, energetic_device_id):
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

async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    logger.info("Starting Energetic Devices WebSocket Server...")
    
    # Запускаем подписку на Redis каналы для получения сообщений
    await websocket_events_manager.init_redis_listener()
    logger.info("Redis listener initialized for WebSocket events")
    
    # Загружаем фоновые задачи broadcast из БД
    await broadcast_manager.load_from_db()
    logger.info("Broadcast tasks loaded from database")

    reload_watcher_task = asyncio.create_task(_watch_broadcast_reload_requests())
    
    yield
    
    # Shutdown
    logger.info("Shutting down Energetic Devices WebSocket Server...")

    reload_watcher_task.cancel()
    try:
        await reload_watcher_task
    except asyncio.CancelledError:
        pass
    
    # Останавливаем все broadcast задачи
    for task_id, task_data in list(broadcast_manager.tasks.items()):
        if "task" in task_data and task_data["task"]:
            task_data["task"].cancel()
    
    logger.info("All broadcast tasks stopped")


app = FastAPI(
    title="Energetic Devices WebSocket Server", 
    description="WebSocket сервер для энергетических устройств (Cerbo/Modbus)",
    lifespan=lifespan
)

# Добавляем CORS middleware для поддержки WebSocket соединений из браузера
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Инициализация контекста для проверки паролей
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Проверяет соответствие plain-text пароля хешированному.
    Standalone функция для избежания циклических импортов.
    """
    return pwd_context.verify(plain_password, hashed_password)


async def get_user_by_email(email: str, db: AsyncSession):
    """
    Получает пользователя по email из базы данных.
    Standalone функция для избежания циклических импортов.
    """
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


def _snapshot_marker(snapshot: dict) -> str:
    return f"{snapshot.get('cached_at')}:{snapshot.get('measured_at')}"


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
                        "group": None,
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

# ==================== PI30 кеширование =====================
@app.get(
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

    return {
        "debug_route_revision": "pi30-debug-v4-worker",
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
# ==================== PI30 кеширование =====================

FIRMWARE_DIR = Path(__file__).resolve().parents[2] / "device_firmware"


def _resolve_firmware_path(request_data: dict) -> Optional[Path]:
    filename = request_data.get("filename") or request_data.get("firmware")
    if filename:
        candidate = (FIRMWARE_DIR / filename).resolve()
        if FIRMWARE_DIR in candidate.parents and candidate.is_file():
            return candidate
        return None

    if not FIRMWARE_DIR.exists():
        return None

    files = [p for p in FIRMWARE_DIR.iterdir() if p.is_file()]
    if not files:
        return None

    return max(files, key=lambda p: p.stat().st_mtime)


async def _send_firmware_over_ws(websocket: WebSocket, firmware_path: Path, request_id: Optional[str] = None):
    size_bytes = firmware_path.stat().st_size
    start_payload = {
        "command": "update_firmware",
        "status": "starting",
        "filename": firmware_path.name,
        "size_bytes": size_bytes,
    }
    if request_id:
        start_payload["request_id"] = request_id

    await websocket.send_json(start_payload)

    with firmware_path.open("rb") as firmware_file:
        payload = firmware_file.read()
        await websocket.send_bytes(payload)

    done_payload = {
        "command": "update_firmware",
        "status": "completed",
        "filename": firmware_path.name,
        "size_bytes": size_bytes,
    }
    if request_id:
        done_payload["request_id"] = request_id
    await websocket.send_json(done_payload)


# ===================== WebSocket эндпоинты =====================
# Оставлены только WebSocket соединения и вспомогательные функции

@app.websocket("/wss/statuses")
async def websocket_cor_bridge_statuses(
    websocket: WebSocket,
):
    """
    Канал статусов COR Bridge в реальном времени.

    Статусы:
    - active: устройство online и на нем есть фоновые polling-задачи
    - stopped: устройство online, но фоновых polling-задач нет (только keepalive)
    - inactive: устройство offline (соединение отсутствует)
    """
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


@app.websocket("/wss/responses")
async def websocket_device_responses(
    websocket: WebSocket,
    device_id: str = None
):
    """
    WebSocket endpoint для фронтенда, получает ответы от энергетических устройств.
    
    **Использование:**
    - `/wss/responses?device_id=COR-B0B21CA3435C` - только ответы от конкретного устройства
    - `/wss/responses` - ответы от всех подключенных устройств
    
    **Формат данных:**
    ```json
    {
        "device_id": "COR-B0B21CA3435C",
        "data": {...},
        "timestamp": "2026-01-02T09:18:41.123456"
    }
    ```
    """
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
                        last_raw_cache_marker = ""
                        await websocket.send_json({
                            "type": "subscription_changed",
                            "device_id": new_device_id,
                            "timestamp": datetime.utcnow().isoformat(),
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
                except Exception:
                    break
    
    except WebSocketDisconnect:
        logger.info(f"Frontend client {connection_id} disconnected normally")
    except Exception as e:
        logger.error(f"Error in frontend device responses connection {connection_id}: {e}", exc_info=True)
    finally:
        # Удаляем клиента из подписчиков
        websocket_events_manager.frontend_subscribers.pop(connection_id, None)
        logger.info(f"Frontend client {connection_id} disconnected")


@app.websocket("/wss/devices")
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
    # Сначала принимаем WebSocket соединение
    await websocket.accept()
    # logger.info(f"Energetic device WebSocket accepted, session_id: {session_id}")
    
    connection_id = await websocket_events_manager.connect(websocket, session_id=session_id, accept_connection=False)
    # logger.info(f"Energetic device connection registered, session_id: {session_id}")
    
    connected_monotonic = time.monotonic()
    auth_received_monotonic = None
    auth_sent_monotonic = None
    device_saved_monotonic = None
    last_device_message_monotonic = None
    last_server_send_monotonic = None
    last_device_message_summary = "none"
    disconnect_phase = "accepted"
    disconnect_code = None
    disconnect_reason = None
    user = None
    device_name = None
    device_protocol = None
    device_description = None
    device_hardware_id = None

    try:
        # Первое сообщение с credentials
        try:
            raw_message = await websocket.receive_text()
            auth_received_monotonic = time.monotonic()
            last_device_message_monotonic = auth_received_monotonic
            last_device_message_summary = f"auth message length={len(raw_message)}"
            disconnect_phase = "auth_received"
            logger.debug(f"Received device auth message: length={len(raw_message)}")
            auth_data = json.loads(raw_message)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in first device auth message: {e}; length={len(raw_message)}")
            await websocket.send_json({"cloud_status": "Auth error: Invalid JSON format"})
            last_server_send_monotonic = time.monotonic()
            disconnect_phase = "auth_invalid_json"
            await websocket_events_manager.disconnect(connection_id)
            return
        
        user_email = auth_data.get("email")
        password = auth_data.get("password")
        device_name = auth_data.get("node_name") or auth_data.get("device_name") or auth_data.get("name")
        device_protocol = auth_data.get("protocol")
        device_description = auth_data.get("description")

        # Определяем итоговый session_id: используем query param, затем session_token/device_id из payload, иначе генерируем
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
            last_server_send_monotonic = time.monotonic()
            disconnect_phase = "auth_missing_credentials"
            await websocket_events_manager.disconnect(connection_id)
            logger.warning(f"Missing credentials for session {session_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing credentials"
            )
        
        # Проверяем пользователя без использования auth_service
        user = await get_user_by_email(email=user_email, db=db)
        if user is None or not verify_password(plain_password=password, hashed_password=user.password):
            await websocket.send_json({"cloud_status": "Auth error: Invalid credentials"})
            last_server_send_monotonic = time.monotonic()
            disconnect_phase = "auth_invalid_credentials"
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
            "connection_id": connection_id,
        })
        auth_sent_monotonic = time.monotonic()
        last_server_send_monotonic = auth_sent_monotonic
        disconnect_phase = "auth_response_sent"
        logger.info(f"Auth response sent to energetic device {session_id}")

        # Регистрируем/обновляем устройство в БД
        energetic_device = await upsert_energetic_device(
            db,
            device_id=device_hardware_id,
            owner_cor_id=user.cor_id,
            name=device_name,
            protocol=device_protocol,
            description=device_description,
            is_active=True,
            last_seen=datetime.utcnow(),
        )
        session_aliases = [str(alias) for alias in {session_id, device_hardware_id, energetic_device.id} if alias]
        await redis_client.hset(
            f"ws:connection:{connection_id}",
            mapping={
                "session_id": session_id,
                "device_id": device_hardware_id,
                "energetic_device_id": energetic_device.id,
                "session_aliases": json.dumps(session_aliases),
            },
        )
        for alias in session_aliases:
            await redis_client.set(f"ws:session:{alias}", connection_id)

        # Keep the same per-device dispatcher alive for on-demand commands,
        # including devices that have no background polling preset.
        broadcast_manager.on_demand_sessions.add(device_hardware_id)
        broadcast_manager._ensure_device_dispatcher(device_hardware_id)
        websocket_events_manager.active_connections[connection_id]["dispatch_session_id"] = device_hardware_id

        command_grace_until = asyncio.get_event_loop().time() + DEVICE_WS_COMMAND_GRACE_SECONDS
        websocket_events_manager.active_connections[connection_id][
            "defer_outbound_until_monotonic"
        ] = command_grace_until

        device_saved_monotonic = time.monotonic()
        disconnect_phase = "device_saved_waiting_for_messages"
        logger.info(
            "Energetic device saved: "
            f"session_id={session_id}, device_id={device_hardware_id}, "
            f"db_id={energetic_device.id}, owner={user.cor_id}, "
            f"command_grace_seconds={DEVICE_WS_COMMAND_GRACE_SECONDS}"
        )
        
        # Основной цикл приема данных.
        # esp_websocket_client has its own WebSocket-level ping/pong. Sending a
        # text "ping" from the server is application data and can make firmware
        # state machines close the socket after successful authentication.
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=60.0)
            except asyncio.TimeoutError:
                disconnect_phase = "idle_waiting_for_messages"
                continue

            if message.get("type") == "websocket.disconnect":
                disconnect_code = message.get("code", status.WS_1000_NORMAL_CLOSURE)
                disconnect_reason = message.get("reason")
                disconnect_phase = "disconnect_message_received"
                raise WebSocketDisconnect(code=disconnect_code)

            raw_text = message.get("text")
            raw_bytes = message.get("bytes")

            if raw_text is None and raw_bytes is None:
                continue

            if raw_text is not None:
                raw_data = raw_text
            else:
                raw_data = raw_bytes.decode("utf-8", errors="replace")

            last_device_message_monotonic = time.monotonic()
            last_device_message_summary = (
                f"text length={len(raw_data)} preview={raw_data[:120]!r}"
                if raw_text is not None
                else f"bytes length={len(raw_bytes or b'')} preview={raw_data[:120]!r}"
            )
            disconnect_phase = "device_message_received"

            if raw_data in {"ping", "pong"}:
                if raw_data == "ping":
                    await websocket.send_text("pong")
                    last_server_send_monotonic = time.monotonic()
                    disconnect_phase = "pong_sent"
                continue

            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                if raw_bytes is not None:
                    data = {
                        "raw_hex": raw_bytes.hex(" "),
                        "raw_text": raw_data,
                    }
                else:
                    logger.warning(
                        f"Invalid non-JSON payload from energetic device {session_id}: {raw_data[:200]}"
                    )
                    continue

            # Только хороший ответ от устройства открывает gate следующей команде.
            # No response from RS485 / NAK оставляют dispatcher ждать timeout.
            if broadcast_manager.is_good_device_response(data):
                activity_aliases = {alias for alias in (session_id, device_hardware_id) if alias}
                for alias in activity_aliases:
                    broadcast_manager.mark_session_activity(alias)
            else:
                activity_aliases = {alias for alias in (session_id, device_hardware_id) if alias}
                for alias in activity_aliases:
                    broadcast_manager.mark_session_bad_response(alias)

            # logger.debug(f"Received data from energetic device {session_id}: {data}")

            if isinstance(data, dict) and data.get("command") == "update_firmware":
                request_id = data.get("request_id")
                firmware_path = _resolve_firmware_path(data)
                if not firmware_path:
                    await websocket.send_json({
                        "command": "update_firmware",
                        "status": "error",
                        "error": "Firmware file not found",
                        "request_id": request_id,
                    })
                else:
                    try:
                        await _send_firmware_over_ws(websocket, firmware_path, request_id=request_id)
                    except Exception as e:
                        logger.error(
                            f"Firmware send failed for {device_hardware_id}: {e}",
                            exc_info=True,
                        )
                        await websocket.send_json({
                            "command": "update_firmware",
                            "status": "error",
                            "error": "Firmware transfer failed",
                            "request_id": request_id,
                        })

            event_timestamp = datetime.utcnow().isoformat()
            frontend_device_id = device_hardware_id or session_id
            device_aliases = {alias for alias in (session_id, frontend_device_id) if alias}
            outbound_data = _with_parsed_pi30_if_enabled(data)
            event_data = {
                "device_id": frontend_device_id,
                "data": data,
                "timestamp": event_timestamp,
            }
            event_data_ws = {
                "device_id": frontend_device_id,
                "data": outbound_data,
                "timestamp": event_timestamp,
            }

            for alias in device_aliases:
                await set_cor_agent_snapshot_cache(alias, event_data)
                await _store_pi30_parsed_cache_if_enabled(alias, event_data)

            await persist_pi30_power_measurement_from_ws(
                device_aliases=device_aliases,
                raw_event=data,
                measured_at=datetime.utcnow(),
            )
            await persist_deye_modbus_power_measurement_from_ws(
                device_aliases=device_aliases,
                raw_event=data,
                measured_at=datetime.utcnow(),
            )
            await persist_tac4300ct_measurement_from_ws(
                device_aliases=device_aliases,
                raw_event=data,
                measured_at=datetime.utcnow(),
            )

            # try:
            #     async with async_session_maker() as write_db:
            #         await try_collect_flywheel_measurement(
            #             write_db,
            #             cache_key=frontend_device_id,
            #             device_aliases=device_aliases,
            #             raw_event=data,
            #             measured_at=datetime.utcnow(),
            #         )
            # except Exception as exc:
            #     logger.warning(
            #         f"Failed to persist flywheel aggregated measurement for device {frontend_device_id}: {exc}"
            #     )

            # Broadcast события всем подключенным клиентам
            await websocket_events_manager.broadcast_event(event_data_ws)

            # Отправляем данные фронтенд клиентам, которые подписаны на этот device_id
            for subscriber_id, subscriber_info in list(websocket_events_manager.frontend_subscribers.items()):
                try:
                    subscriber_device_id = subscriber_info.get("device_id")
                    # Отправляем если подписан на все устройства или именно на это
                    if subscriber_device_id is None or subscriber_device_id in device_aliases:
                        await subscriber_info["websocket"].send_json(event_data_ws)
                except Exception as e:
                    # logger.debug(f"Failed to send to frontend subscriber {subscriber_id}: {e}")
                    websocket_events_manager.frontend_subscribers.pop(subscriber_id, None)
    
    except WebSocketDisconnect as e:
        disconnect_code = disconnect_code if disconnect_code is not None else getattr(e, "code", None)
        disconnect_reason = disconnect_reason or getattr(e, "reason", None)
        now_monotonic = time.monotonic()

        def elapsed_ms(timestamp):
            return round((now_monotonic - timestamp) * 1000, 1) if timestamp is not None else None

        logger.info(
            "Energetic device websocket disconnected: "
            f"session_id={session_id}, device_id={device_hardware_id}, "
            f"connection_id={connection_id}, code={disconnect_code}, "
            f"reason={disconnect_reason}, "
            f"code_description={_describe_websocket_close_code(disconnect_code)}, "
            f"phase={disconnect_phase}, "
            f"lifetime_ms={elapsed_ms(connected_monotonic)}, "
            f"since_auth_received_ms={elapsed_ms(auth_received_monotonic)}, "
            f"since_auth_response_sent_ms={elapsed_ms(auth_sent_monotonic)}, "
            f"since_device_saved_ms={elapsed_ms(device_saved_monotonic)}, "
            f"since_last_device_message_ms={elapsed_ms(last_device_message_monotonic)}, "
            f"since_last_server_send_ms={elapsed_ms(last_server_send_monotonic)}, "
            f"last_device_message={last_device_message_summary}"
        )
    except HTTPException as e:
        logger.error(f"Authentication failed for session {session_id}: {e.detail}")
    except Exception as e:
        logger.error(f"Error in energetic device connection {session_id}: {e}", exc_info=True)
    finally:
        dispatch_session_id = websocket_events_manager.active_connections.get(connection_id, {}).get("dispatch_session_id")
        if dispatch_session_id and not any(
            conn_id != connection_id and conn.get("dispatch_session_id") == dispatch_session_id
            for conn_id, conn in websocket_events_manager.active_connections.items()
        ):
            broadcast_manager.on_demand_sessions.discard(dispatch_session_id)
        if user:
            try:
                await upsert_energetic_device(
                    db,
                    device_id=device_hardware_id or session_id,
                    owner_cor_id=user.cor_id,
                    name=device_name or session_id,
                    protocol=device_protocol,
                    description=device_description,
                    is_active=False,
                    last_seen=datetime.utcnow(),
                )
            except Exception as e:
                logger.warning(f"Failed to mark energetic device {session_id} offline: {e}")
        await websocket_events_manager.disconnect(connection_id)
        logger.info(f"Energetic device {session_id} disconnected")


@app.post(
    "/broadcast_modbus_command",
    status_code=status.HTTP_200_OK,
    summary="Вручную отправить Modbus-команду всем устройствам"
)
async def broadcast_modbus_command_manual(hex_data: str = "09 03 00 00 00 08 05 48"):
    """
    Вручную отправляет Modbus-команду (hex) всем подключенным энергетическим устройствам.
    По умолчанию отправляет: 09 03 00 00 00 08 05 48
    """
    connections = websocket_events_manager.active_connections
    
    if not connections:
        raise HTTPException(status_code=404, detail="No active energetic devices connected")
    
    command_data = {
        "command_type": "modbus_read",
        "data": hex_data
    }
    
    sent_count = 0
    failed_count = 0
    
    for connection_id, conn_data in connections.items():
        session_id = conn_data.get("session_id")
        if not session_id:
            continue
        
        try:
            await websocket_events_manager.send_to_session(
                session_id=session_id,
                event_data=command_data
            )
            sent_count += 1
            # logger.info(f"📤 Manual Modbus command sent to session_id: {session_id}")
        except Exception as e:
            failed_count += 1
            logger.warning(f"Failed to send manual command to session {session_id}: {e}")
    
    return {
        "detail": "Modbus command broadcast complete",
        "total_devices": len(connections),
        "sent_successfully": sent_count,
        "failed": failed_count,
        "hex_command": hex_data
    }


# ===================== Дублирующие роуты для NGINX без trailing slash =====================

@app.websocket("/wssresponses")
async def websocket_device_responses_no_slash(
    websocket: WebSocket,
    device_id: str = None
):
    """Дублирующий endpoint для /wss/responses (когда NGINX не добавляет slash)"""
    await websocket_device_responses(websocket, device_id)


@app.websocket("/wssdevices")
async def websocket_energetic_device_endpoint_no_slash(
    websocket: WebSocket,
    session_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Дублирующий endpoint для /wss/devices (когда NGINX не добавляет slash)"""
    await websocket_energetic_device_endpoint(websocket, session_id, db)


@app.websocket("/wssstatuses")
async def websocket_cor_bridge_statuses_no_slash(
    websocket: WebSocket,
):
    """Дублирующий endpoint для /wss/statuses (когда NGINX не добавляет slash)"""
    await websocket_cor_bridge_statuses(websocket)
