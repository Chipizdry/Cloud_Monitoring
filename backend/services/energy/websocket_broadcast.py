"""
Менеджер фоновых задач для периодической рассылки команд энергетическим устройствам.
Используется в websocket_routes.py и device_proxy.py для управления broadcast задачами.
"""
import asyncio
import json
import math
import re
import time
from uuid import uuid4
from typing import Dict
from loguru import logger
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select, delete, or_

from backend.database.models.energy import WebSocketBroadcastTask, EnergeticObject, EnergeticDevice
from backend.database.db import async_session_maker
from backend.database.redis_db import redis_client

from backend.schemas.websocket import WebSocketBroadcastTaskCreate
from backend.services.energy.pi30_commands import (
    PI30_COMMAND_DESCRIPTIONS,
    PI30Command,
    format_pi30_command_with_crc_hex,
)
from backend.services.energy.pi30_parser import parse_pi30_event
from backend.services.shared.websocket_events_manager import websocket_events_manager
from backend.services.energy.inverter_preset_loader import (
    build_broadcast_task_records,
    get_preset,
)


def _looks_like_hex(value: str) -> bool:
    compact = "".join(value.split())
    return len(compact) >= 2 and len(compact) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", compact) is not None


BAD_DEVICE_RESPONSE_STATUSES = {"no_response", "nak"}
BAD_DEVICE_RESPONSE_STRINGS = {
    "modbus timeout",
    "nak",
    "nack",
    "no response",
    "no response from rs485",
    "rs485 timeout",
    "timeout",
}

# Temporary debug bypass for a bridge with unstable firmware/RS485 behavior.
# Remove the session id from this set to restore normal response gating.
DEBUG_RESPONSE_GATE_BYPASS_SESSION_IDS = {"COR-08D1F99A1AD0"}

ON_DEMAND_QUEUE_TTL_SECONDS = 300


def on_demand_queue_key(session_id: str) -> str:
    return f"ws:device:{session_id}:on_demand"


async def enqueue_on_demand_command(session_id: str, payload: dict) -> str | None:
    """Queue a command for the WebSocket worker that owns the device dispatcher."""
    if not await redis_client.get(f"ws:session:{session_id}"):
        return None
    request_id = str(uuid4())
    item = json.dumps({
        "request_id": request_id,
        "payload": payload,
        "queued_at": time.time(),
    })
    key = on_demand_queue_key(session_id)
    await redis_client.rpush(key, item)
    await redis_client.expire(key, ON_DEMAND_QUEUE_TTL_SECONDS)
    return request_id


# Менеджер пользовательских фоновых рассылок команд с поддержкой БД
class BroadcastTaskManager:
    """Менеджер задач периодической рассылки команд на конкретные устройства через WebSocket.
    
    - Каждая задача независимо спит(interval) -> отправляет -> ждет ответ
    - Несколько задач на одно device сериализуются lock'ом
    - При старте разбросаны на 1 сек для избежание наплыва команд
    """
    def __init__(self):
        # db_task_id -> {"task": asyncio.Task, "db_task": WebSocketBroadcastTask}
        self.tasks: Dict[str, Dict] = {}
        # session_id -> asyncio.Queue - очередь команд для устройства
        # Каждая задача только enqueue, единый диспетчер деqueue и отправляет
        self.device_queues: Dict[str, asyncio.Queue] = {}
        # session_id -> set[task_id] - задачи, которые уже стоят в очереди или выполняются.
        self.device_pending_tasks: Dict[str, set[str]] = {}
        # session_id -> asyncio.Task - единственный диспетчер на устройство
        self.device_dispatchers: Dict[str, asyncio.Task] = {}
        # Connected devices can receive manual commands even without background tasks.
        self.on_demand_sessions: set[str] = set()
        # session_id -> version of last inbound device activity
        self.session_activity_version: Dict[str, int] = {}
        # session_id -> event set when device sends any next message
        self.session_activity_events: Dict[str, asyncio.Event] = {}
        # session_id -> version of last bad inbound device response
        self.session_bad_response_version: Dict[str, int] = {}
        # session_id -> consecutive bad response/timeout count
        self.session_bad_response_count: Dict[str, int] = {}
        # session_id -> monotonic timestamp before which next command should not be sent
        self.session_bad_response_cooldown_until: Dict[str, float] = {}
        # Не блокируем очередь команд бесконечно: если ответ не пришёл,
        # через таймаут всё равно продолжаем следующий цикл.
        self.command_response_timeout: float | None = 8.0
        self.bad_response_cooldown_base_seconds = 8.0
        self.bad_response_cooldown_step_seconds = 2.0
        self.bad_response_cooldown_max_seconds = 20.0
        # Broadcast-задачи исполняются только в отдельном websocket worker,
        # поэтому межпроцессная Redis-блокировка отправки по device не нужна.
        self.use_redis_device_send_lock = False
        # Защита от зависшего Redis-lock на отправку между процессами.
        self.device_send_lock_ttl_seconds = 30
        # Защищает от конкурентных reload/start/stop, которые могут породить дубли раннеров.
        self._reload_lock = asyncio.Lock()

    @staticmethod
    def is_good_device_response(payload) -> bool:
        """Returns False for device-level failures that should not unblock command flow."""
        parsed_pi30 = parse_pi30_event(payload) if isinstance(payload, dict) else None
        if parsed_pi30 and parsed_pi30.get("status") in BAD_DEVICE_RESPONSE_STATUSES:
            return False

        for value in BroadcastTaskManager._iter_response_strings(payload):
            normalized = value.strip().lower()
            if not normalized:
                continue
            if normalized in BAD_DEVICE_RESPONSE_STRINGS:
                return False
            if any(bad in normalized for bad in ("no response from rs485", "modbus timeout", "rs485 timeout")):
                return False

            if _looks_like_hex(value):
                try:
                    ascii_value = bytes.fromhex("".join(value.split())).decode("ascii", errors="ignore")
                except ValueError:
                    continue
                ascii_normalized = ascii_value.strip().replace("(", "").replace(")", "").lower()
                if ascii_normalized in {"nak", "nack"}:
                    return False

        return True

    @staticmethod
    def _iter_response_strings(payload):
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in {"hex_response", "hex_data", "response", "raw_response", "error", "status"} and isinstance(value, str):
                    yield value
                elif isinstance(value, (dict, list)):
                    yield from BroadcastTaskManager._iter_response_strings(value)
        elif isinstance(payload, list):
            for item in payload:
                yield from BroadcastTaskManager._iter_response_strings(item)

    def _bad_response_cooldown_seconds(self, failure_count: int) -> float:
        cooldown = (
            self.bad_response_cooldown_base_seconds
            + max(0, failure_count - 1) * self.bad_response_cooldown_step_seconds
        )
        return min(cooldown, self.bad_response_cooldown_max_seconds)

    @staticmethod
    def _bypass_response_gate_for_session(session_id: str) -> bool:
        return session_id in DEBUG_RESPONSE_GATE_BYPASS_SESSION_IDS

    def mark_session_bad_response(self, session_id: str) -> float:
        if self._bypass_response_gate_for_session(session_id) and session_id not in self.on_demand_sessions:
            self.session_bad_response_count.pop(session_id, None)
            self.session_bad_response_cooldown_until.pop(session_id, None)
            return 0.0

        failure_count = self.session_bad_response_count.get(session_id, 0) + 1
        cooldown_seconds = self._bad_response_cooldown_seconds(failure_count)

        self.session_bad_response_count[session_id] = failure_count
        self.session_bad_response_version[session_id] = self.session_bad_response_version.get(session_id, 0) + 1
        self.session_bad_response_cooldown_until[session_id] = asyncio.get_event_loop().time() + cooldown_seconds
        self._get_session_activity_event(session_id).set()
        return cooldown_seconds

    @staticmethod
    def _device_send_lock_key(session_id: str) -> str:
        """Redis ключ глобальной блокировки отправки для устройства (межпроцессная синхронизация)."""
        return f"ws:broadcast:device:{session_id}:sending"

    async def _acquire_device_send_lock(self, session_id: str) -> bool:
        """Попытка захватить Redis-блокировку отправки для устройства (NX = один из процессов)."""
        key = self._device_send_lock_key(session_id)
        ttl = self.device_send_lock_ttl_seconds
        try:
            acquired = await redis_client.set(key, "1", ex=ttl, nx=True)
            return bool(acquired)
        except Exception:
            return True  # Redis недоступен - разрешаем отправку

    async def _release_device_send_lock(self, session_id: str) -> None:
        """Освободить Redis-блокировку отправки для устройства."""
        try:
            await redis_client.delete(self._device_send_lock_key(session_id))
        except Exception:
            pass

    async def _wait_for_device_send_lock(self, session_id: str, timeout: float = 60.0) -> bool:
        """Ждать пока Redis-блокировка освободится и захватить её. Polling 100ms."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self._acquire_device_send_lock(session_id):
                return True
            remaining = deadline - loop.time()
            await asyncio.sleep(min(0.1, remaining))
        return False

    def _get_device_queue(self, session_id: str) -> asyncio.Queue:
        if session_id not in self.device_queues:
            self.device_queues[session_id] = asyncio.Queue()
        return self.device_queues[session_id]

    async def _pop_on_demand_command(self, session_id: str) -> dict | None:
        try:
            raw = await redis_client.lpop(on_demand_queue_key(session_id))
        except Exception as exc:
            logger.warning(f"Failed to read on-demand queue for {session_id}: {exc}")
            return None
        if raw is None:
            return None
        try:
            item = json.loads(raw)
            if time.time() - float(item["queued_at"]) > ON_DEMAND_QUEUE_TTL_SECONDS:
                logger.warning(f"Discarding expired on-demand command for {session_id}")
                return None
            if not isinstance(item["payload"], dict):
                raise ValueError("payload must be an object")
            if not isinstance(item["request_id"], str):
                raise ValueError("request_id must be a string")
            return item
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning(f"Discarding invalid on-demand command for {session_id}: {exc}")
            return None

    def _get_device_pending_tasks(self, session_id: str) -> set[str]:
        pending = self.device_pending_tasks.get(session_id)
        if pending is None:
            pending = set()
            self.device_pending_tasks[session_id] = pending
        return pending

    def _ensure_device_dispatcher(self, session_id: str) -> None:
        """Запускает диспетчер для устройства если его нет или он завершился."""
        existing = self.device_dispatchers.get(session_id)
        if existing is None or existing.done():
            t = asyncio.create_task(self._device_dispatcher(session_id))
            self.device_dispatchers[session_id] = t

    async def _device_dispatcher(self, session_id: str) -> None:
        """
        Единственный диспетчер команд для одного устройства.

        - Максимум 1 команда в полёте в любой момент времени
        - Следующая команда не отправляется до получения ответа на предыдущую
        - Ручные команды идут перед ожидающими фоновыми, каждая группа FIFO
        - Межпроцессная синхронизация через Redis device-send-lock
        """
        queue = self._get_device_queue(session_id)
        deferred_background = None

        while True:
            try:
                manual = await self._pop_on_demand_command(session_id)
                if manual is not None:
                    item = {"task_id": manual["request_id"], "db_task": None, "payload": manual["payload"]}
                elif deferred_background is not None:
                    item = deferred_background
                    deferred_background = None
                else:
                    item = await asyncio.wait_for(queue.get(), timeout=0.2)
                    manual = await self._pop_on_demand_command(session_id)
                    if manual is not None:
                        deferred_background = item
                        item = {"task_id": manual["request_id"], "db_task": None, "payload": manual["payload"]}
            except asyncio.TimeoutError:
                if (
                    session_id not in self.on_demand_sessions
                    and not any(info["db_task"].session_id == session_id for info in self.tasks.values())
                ):
                    break
                continue
            except asyncio.CancelledError:
                break

            task_id = item["task_id"]
            db_task = item["db_task"]
            is_manual = db_task is None
            payload = item["payload"]
            pending_tasks = self._get_device_pending_tasks(session_id)
            bypass_response_gate = (
                not is_manual
                and self._bypass_response_gate_for_session(session_id)
                and session_id not in self.on_demand_sessions
            )

            try:
                if not is_manual and task_id not in self.tasks:
                    continue

                if not is_manual and not await self._is_task_active_in_db(task_id):
                    logger.info(
                        f"[{db_task.task_name}] Skipping send because task {task_id} is inactive in DB"
                    )
                    self.tasks.pop(task_id, None)
                    await self._clear_running(task_id)
                    continue

                if not bypass_response_gate:
                    await self._wait_for_session_cooldown(session_id)

                if not is_manual and not await self._acquire_send_slot(db_task):
                    logger.debug(
                        f"[{db_task.task_name}] Skipping duplicate slot from another process"
                    )
                    continue

                try:
                    if self.use_redis_device_send_lock:
                        lock_acquired = await self._wait_for_device_send_lock(session_id, timeout=30.0)
                        if not lock_acquired:
                            logger.warning(
                                f"[{db_task.task_name}] Timeout waiting for device send lock on {session_id}"
                            )
                            continue

                    activity_version = self.session_activity_version.get(session_id, 0)
                    bad_response_version = self.session_bad_response_version.get(session_id, 0)

                    sent = await websocket_events_manager.send_to_session(
                        session_id=session_id,
                        event_data=payload,
                    )

                    if not sent:
                        if not is_manual:
                            await self._mark_running(task_id, db_task.interval_ms, session_id)
                        continue

                    if not is_manual:
                        await self._mark_running(task_id, db_task.interval_ms, session_id)

                    if bypass_response_gate:
                        logger.info(
                            f"[{db_task.task_name}] Response gate bypass enabled for {session_id}; "
                            "skipping response wait and bad-response cooldown"
                        )
                        continue

                    response_status = await self._wait_for_session_response(
                        session_id=session_id,
                        previous_activity_version=activity_version,
                        previous_bad_response_version=bad_response_version,
                        timeout_seconds=self._current_response_timeout(session_id),
                    )
                    if response_status == "bad_response":
                        cooldown_seconds = self._bad_response_cooldown_seconds(
                            self.session_bad_response_count.get(session_id, 1)
                        )
                        logger.warning(
                            f"[{db_task.task_name if db_task else task_id}] Bad device response from {session_id}; "
                            f"cooling down for {cooldown_seconds:.2f}s"
                        )
                        await self._wait_for_session_cooldown(session_id)
                    elif response_status != "activity":
                        logger.warning(
                            f"[{db_task.task_name if db_task else task_id}] No device response from {session_id} "
                            f"while waiting for next command gate"
                        )
                        await self._wait_for_session_cooldown(session_id)
                finally:
                    if self.use_redis_device_send_lock:
                        await self._release_device_send_lock(session_id)

            except asyncio.CancelledError:
                if self.use_redis_device_send_lock:
                    await self._release_device_send_lock(session_id)
                break
            except Exception as e:
                logger.error(f"Device dispatcher error for {session_id}: {e}", exc_info=True)
                if self.use_redis_device_send_lock:
                    await self._release_device_send_lock(session_id)
            finally:
                if not is_manual:
                    pending_tasks.discard(task_id)
                    queue.task_done()

        self.device_dispatchers.pop(session_id, None)

    @staticmethod
    def _running_heartbeat_key(task_id: str) -> str:
        return f"ws:broadcast:task:{task_id}:running"

    @staticmethod
    def _running_heartbeat_ttl(interval_ms: float) -> int:
        base_ttl = int(interval_ms / 1000 * 3) + 5
        return max(30, base_ttl)

    @staticmethod
    def _send_slot_ttl(interval_ms: float) -> int:
        base_ttl = math.ceil(interval_ms / 1000 * 2) + 5
        return max(10, base_ttl)

    @staticmethod
    def _task_anchor_timestamp(db_task: WebSocketBroadcastTask) -> float:
        created_at = getattr(db_task, "created_at", None)
        if created_at is None:
            return 0.0
        try:
            return float(created_at.timestamp())
        except Exception:
            return 0.0

    def _get_session_activity_event(self, session_id: str) -> asyncio.Event:
        event = self.session_activity_events.get(session_id)
        if event is None:
            event = asyncio.Event()
            self.session_activity_events[session_id] = event
        return event

    def mark_session_activity(self, session_id: str) -> None:
        self.session_activity_version[session_id] = self.session_activity_version.get(session_id, 0) + 1
        self.session_bad_response_count.pop(session_id, None)
        self.session_bad_response_cooldown_until.pop(session_id, None)
        self._get_session_activity_event(session_id).set()

    async def _wait_for_session_cooldown(self, session_id: str) -> None:
        cooldown_until = self.session_bad_response_cooldown_until.get(session_id)
        if cooldown_until is None:
            return

        remaining = cooldown_until - asyncio.get_event_loop().time()
        if remaining <= 0:
            return

        logger.info(f"Waiting {remaining:.2f}s bad-response cooldown before next command for {session_id}")
        await asyncio.sleep(remaining)

    def _current_response_timeout(self, session_id: str) -> float | None:
        if self.command_response_timeout is None:
            return None
        failure_count = self.session_bad_response_count.get(session_id, 0)
        return self._bad_response_cooldown_seconds(failure_count + 1)

    async def _wait_for_session_activity(
        self,
        session_id: str,
        previous_version: int,
        timeout_seconds: float | None,
    ) -> bool:
        return await self._wait_for_session_response(
            session_id=session_id,
            previous_activity_version=previous_version,
            previous_bad_response_version=self.session_bad_response_version.get(session_id, 0),
            timeout_seconds=timeout_seconds,
        ) == "activity"

    async def _wait_for_session_response(
        self,
        session_id: str,
        previous_activity_version: int,
        previous_bad_response_version: int,
        timeout_seconds: float | None,
    ) -> str:
        if self.session_activity_version.get(session_id, 0) > previous_activity_version:
            return "activity"
        if self.session_bad_response_version.get(session_id, 0) > previous_bad_response_version:
            return "bad_response"

        event = self._get_session_activity_event(session_id)
        loop = asyncio.get_event_loop()
        deadline = None if timeout_seconds is None else loop.time() + max(0.0, timeout_seconds)

        while True:
            event.clear()

            if self.session_activity_version.get(session_id, 0) > previous_activity_version:
                return "activity"
            if self.session_bad_response_version.get(session_id, 0) > previous_bad_response_version:
                return "bad_response"

            if deadline is None:
                await event.wait()
                if self.session_activity_version.get(session_id, 0) > previous_activity_version:
                    return "activity"
                if self.session_bad_response_version.get(session_id, 0) > previous_bad_response_version:
                    return "bad_response"
                continue

            remaining = deadline - loop.time()
            if remaining <= 0:
                self.mark_session_bad_response(session_id)
                return "timeout"

            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                self.mark_session_bad_response(session_id)
                return "timeout"

            if self.session_activity_version.get(session_id, 0) > previous_activity_version:
                return "activity"
            if self.session_bad_response_version.get(session_id, 0) > previous_bad_response_version:
                return "bad_response"

    async def _acquire_send_slot(self, db_task: WebSocketBroadcastTask, now_ts: float | None = None) -> bool:
        interval_ms = float(db_task.interval_ms or 0)
        if interval_ms <= 0:
            return True

        if now_ts is None:
            now_ts = time.time()

        anchor_ts = self._task_anchor_timestamp(db_task)
        elapsed = max(0.0, now_ts - anchor_ts)
        slot_index = int(elapsed // (interval_ms / 1000))
        key = f"ws:broadcast:task:{db_task.id}:slot:{slot_index}"
        ttl = self._send_slot_ttl(interval_ms)

        try:
            acquired = await redis_client.set(key, "1", ex=ttl, nx=True)
            return bool(acquired)
        except Exception as e:
            logger.warning(
                f"Failed to acquire send slot for task {db_task.id}: {e}. Allowing send to avoid stall."
            )
            return True

    async def _mark_running(self, task_id: str, interval_ms: float, session_id: str) -> None:
        try:
            key = self._running_heartbeat_key(task_id)
            ttl = self._running_heartbeat_ttl(interval_ms)
            payload = {
                "status": "running",
                "session_id": session_id,
                "updated_at": asyncio.get_event_loop().time(),
            }
            await redis_client.setex(key, ttl, str(payload))
        except Exception as e:
            logger.warning(f"Failed to set running heartbeat for task {task_id}: {e}")

    async def _clear_running(self, task_id: str) -> None:
        try:
            await redis_client.delete(self._running_heartbeat_key(task_id))
        except Exception as e:
            logger.warning(f"Failed to clear running heartbeat for task {task_id}: {e}")

    async def _is_running_redis(self, task_id: str) -> bool:
        try:
            exists = await redis_client.exists(self._running_heartbeat_key(task_id))
            return bool(exists)
        except Exception:
            return False

    async def _is_task_active_in_db(self, task_id: str) -> bool:
        try:
            async with async_session_maker() as db:
                result = await db.execute(
                    select(WebSocketBroadcastTask.is_active).where(WebSocketBroadcastTask.id == task_id)
                )
                is_active = result.scalar_one_or_none()
                return bool(is_active)
        except Exception as e:
            logger.warning(f"Failed to check broadcast task activity in DB for {task_id}: {e}")
            return True

    @staticmethod
    def _decode_pi30_human_command(pi30_hex: str | None) -> tuple[str | None, str | None]:
        """Декодирует PI30 hex payload в команду (например QPIGS) и описание."""
        if not pi30_hex:
            return None, None

        try:
            compact = str(pi30_hex).replace(" ", "")
            raw = bytes.fromhex(compact)
            if not raw:
                return None, None

            if raw[-1] == 0x0D:
                raw = raw[:-1]

            if len(raw) >= 3:
                command_bytes = raw[:-2]
            else:
                command_bytes = raw

            command_text = command_bytes.decode("ascii", errors="ignore").strip()
            if not command_text:
                return None, None

            description = PI30_COMMAND_DESCRIPTIONS.get(PI30Command(command_text), None)
            return command_text, description
        except Exception:
            return None, None

    @staticmethod
    def _normalize_update_data(update_data):
        if hasattr(update_data, "model_dump"):
            return update_data.model_dump(exclude_unset=True)
        return dict(update_data)

    @staticmethod
    def _task_config_changed(current_task: WebSocketBroadcastTask, new_task: WebSocketBroadcastTask) -> bool:
        return any(
            (
                current_task.task_name != new_task.task_name,
                current_task.session_id != new_task.session_id,
                current_task.command_type != new_task.command_type,
                (current_task.command_payload or {}) != (new_task.command_payload or {}),
                current_task.interval_ms != new_task.interval_ms,
                current_task.is_active != new_task.is_active,
            )
        )

    async def _stop_local_task(self, task_id: str, *, clear_running: bool = True):
        session_id = None
        if task_id in self.tasks:
            session_id = self.tasks[task_id]["db_task"].session_id
            self.tasks[task_id]["task"].cancel()
            try:
                await self.tasks[task_id]["task"]
            except asyncio.CancelledError:
                pass
            del self.tasks[task_id]

        if clear_running:
            await self._clear_running(task_id)

        if session_id:
            still_has_tasks = any(
                info["db_task"].session_id == session_id for info in self.tasks.values()
            )
            if not still_has_tasks and session_id not in self.on_demand_sessions:
                dispatcher = self.device_dispatchers.pop(session_id, None)
                if dispatcher and not dispatcher.done():
                    dispatcher.cancel()
                    try:
                        await dispatcher
                    except (asyncio.CancelledError, Exception):
                        pass
                self.device_queues.pop(session_id, None)
                self.session_activity_version.pop(session_id, None)
                self.session_activity_events.pop(session_id, None)

    async def load_from_db(self):
        """Загружает активные задачи из БД и запускает их"""
        await self.reload_from_db()

    async def _normalize_bridge_ids(self, db, bridge_ids: list[str]) -> list[str]:
        if not bridge_ids:
            return []

        normalized_input = [str(bridge_id).strip() for bridge_id in bridge_ids if bridge_id]
        if not normalized_input:
            return []

        result = await db.execute(
            select(EnergeticDevice).where(
                or_(
                    EnergeticDevice.id.in_(normalized_input),
                    EnergeticDevice.device_id.in_(normalized_input),
                )
            )
        )
        devices = result.scalars().all()

        id_map: dict[str, str] = {}
        for device in devices:
            id_map[str(device.id)] = device.device_id
            id_map[device.device_id] = device.device_id

        normalized = [id_map.get(item, item) for item in normalized_input]
        return list(dict.fromkeys(normalized))

    async def _get_known_bridge_session_ids(self, db) -> list[str]:
        result = await db.execute(
            select(EnergeticDevice.device_id).where(EnergeticDevice.device_id.is_not(None))
        )
        bridge_ids = [str(device_id).strip() for device_id in result.scalars().all() if device_id]
        return list(dict.fromkeys(bridge_ids))

    async def _sync_bridge_tasks_with_presets(self):
        """
        Rebuild auto preset-managed bridge tasks in DB.

        This keeps DB tasks aligned with current preset files after deployment/restart and
        removes stale tasks left after bridge reassignments and
        inverter vendor/model changes.
        """
        async with async_session_maker() as db:
            objects_result = await db.execute(
                select(EnergeticObject)
            )
            objects = objects_result.scalars().all()

            desired_records: list[dict] = []
            managed_sessions: set[str] = set()
            session_owner: dict[str, str] = {}

            for obj in objects:
                if not (obj.vendor and obj.model_name and obj.cor_bridges):
                    continue

                preset = get_preset(obj.vendor, obj.model_name)
                if not preset:
                    continue

                normalized_bridges = await self._normalize_bridge_ids(db, list(obj.cor_bridges or []))
                if not normalized_bridges:
                    continue

                slave_id = (obj.slave_ids[0] if obj.slave_ids else 1)

                for bridge_session_id in normalized_bridges:
                    owner = session_owner.get(bridge_session_id)
                    if owner and owner != obj.id:
                        logger.warning(
                            f"Bridge session '{bridge_session_id}' is linked to multiple objects "
                            f"({owner}, {obj.id}); keeping first owner during preset sync"
                        )
                        continue

                    session_owner[bridge_session_id] = obj.id
                    managed_sessions.add(bridge_session_id)

                    desired_records.extend(
                        build_broadcast_task_records(
                            preset=preset,
                            session_id=bridge_session_id,
                            slave_id=slave_id,
                            host=obj.ip_address,
                            port=obj.port,
                            task_name_prefix=f"{obj.name}:{bridge_session_id}",
                            created_by=obj.owner_cor_id,
                            is_active=bool(obj.is_active),
                        )
                    )

            known_bridge_session_ids = await self._get_known_bridge_session_ids(db)
            preset_managed_sessions = sorted(set(known_bridge_session_ids) | managed_sessions)
            if not preset_managed_sessions:
                return

            session_pattern_filters = [
                WebSocketBroadcastTask.task_name.like(f"%:{session_id}:%")
                for session_id in preset_managed_sessions
            ]
            managed_filter = (
                WebSocketBroadcastTask.command_type.in_(["modbus_read", "modbus_tcp", "pi30"]),
                WebSocketBroadcastTask.session_id.in_(preset_managed_sessions),
                or_(*session_pattern_filters),
            )

            existing_result = await db.execute(
                select(
                    WebSocketBroadcastTask.id,
                    WebSocketBroadcastTask.session_id,
                    WebSocketBroadcastTask.task_name,
                    WebSocketBroadcastTask.is_active,
                ).where(*managed_filter)
            )
            existing_by_key = {
                (row.session_id, row.task_name): row
                for row in existing_result.all()
            }

            for record in desired_records:
                existing_task = existing_by_key.get((record["session_id"], record["task_name"]))
                if not existing_task:
                    continue
                record["id"] = existing_task.id
                record["is_active"] = existing_task.is_active

            await db.execute(
                delete(WebSocketBroadcastTask).where(*managed_filter)
            )

            for record in desired_records:
                db.add(WebSocketBroadcastTask(**record))

            await db.commit()
            logger.info(
                f"Synced preset-managed bridge tasks: sessions={len(managed_sessions)}, "
                f"tasks={len(desired_records)}"
            )

    async def reload_from_db(self):
        """Синхронизирует локальные broadcast-задачи с активными задачами из БД.
        
        При новых задачах на одно устройство разбрасывает их старт на интервал 1 сек
        чтобы избежать наплыва команд при перезагрузке.
        """
        async with self._reload_lock:
            try:
                try:
                    await self._sync_bridge_tasks_with_presets()
                except Exception as e:
                    logger.error(f"Error syncing preset-managed broadcast tasks: {e}", exc_info=True)

                async with async_session_maker() as db:
                    result = await db.execute(
                        select(WebSocketBroadcastTask).where(WebSocketBroadcastTask.is_active == True)
                    )
                    db_tasks = result.scalars().all()

                db_tasks_by_id = {db_task.id: db_task for db_task in db_tasks}
                active_task_ids = set(db_tasks_by_id)

                for task_id in list(self.tasks.keys()):
                    if task_id not in active_task_ids:
                        await self._stop_local_task(task_id)

                session_startup_count: Dict[str, int] = {}

                started = 0
                restarted = 0
                for task_id, db_task in db_tasks_by_id.items():
                    if task_id not in self.tasks:
                        # Каждая следующая задача на device получит +1 сек задержки
                        initial_delay_index = session_startup_count.get(db_task.session_id, 0)
                        session_startup_count[db_task.session_id] = initial_delay_index + 1
                        await self._start_task(db_task, initial_delay_index=initial_delay_index)
                        started += 1
                        continue

                    current_task = self.tasks[task_id]["db_task"]
                    if self._task_config_changed(current_task, db_task):
                        await self._stop_local_task(task_id)
                        await self._start_task(db_task)
                        restarted += 1
                    else:
                        self.tasks[task_id]["db_task"] = db_task

                logger.info(
                    f"📂 Reloaded broadcast tasks from DB: active={len(db_tasks)}, "
                    f"started={started}, restarted={restarted}, local={len(self.tasks)}"
                )
            except Exception as e:
                logger.error(f"Error reloading broadcast tasks from DB: {e}", exc_info=True)

    async def _start_task(self, db_task: WebSocketBroadcastTask, initial_delay_index: int = 0):
        """Внутренний метод для запуска задачи.
        
        Args:
            db_task: Задача из БД
            initial_delay_index: Индекс для разброса при перезагрузке (в % от интервала)
        """
        task_id = db_task.id
        session_id = db_task.session_id
        interval = db_task.interval_ms
        initial_delay = initial_delay_index * (interval / 1000 * 0.2)  # Разброс на 20% интервала между задачами

        if task_id in self.tasks:
            await self._stop_local_task(task_id, clear_running=False)

        self._get_device_queue(session_id)
        self._ensure_device_dispatcher(session_id)

        payload = {
            "command_type": db_task.command_type,
            **db_task.command_payload,
        }

        await self._mark_running(task_id, interval, session_id)

        async def _runner():
            if initial_delay > 0:
                logger.info(f"[{db_task.task_name}] Staggered startup: delaying first run by {initial_delay}s")
                await asyncio.sleep(initial_delay)

            while True:
                try:
                    current_entry = self.tasks.get(task_id)
                    if not current_entry or current_entry.get("task") is not asyncio.current_task():
                        logger.info(f"Broadcast task '{db_task.task_name}' replaced by newer runner")
                        break

                    await self._mark_running(task_id, interval, session_id)
                    await asyncio.sleep(interval / 1000)

                    queue = self._get_device_queue(session_id)
                    pending_tasks = self._get_device_pending_tasks(session_id)
                    self._ensure_device_dispatcher(session_id)
                    if task_id in pending_tasks:
                        continue
                    try:
                        pending_tasks.add(task_id)
                        queue.put_nowait({
                            "task_id": task_id,
                            "db_task": db_task,
                            "payload": payload,
                        })
                    except asyncio.QueueFull:
                        pending_tasks.discard(task_id)
                        logger.debug(f"[{db_task.task_name}] Device queue full, skipping this cycle")

                except asyncio.CancelledError:
                    logger.info(f"Broadcast task '{db_task.task_name}' cancelled")
                    await self._clear_running(task_id)
                    break
                except Exception as e:
                    logger.error(f"[{db_task.task_name}] Error in broadcast task: {e}", exc_info=True)
                    await asyncio.sleep(3)
            await self._clear_running(task_id)

        t = asyncio.create_task(_runner())
        self.tasks[task_id] = {"task": t, "db_task": db_task}

    async def create_and_start(
        self,
        task_data: WebSocketBroadcastTaskCreate,
        *,
        run_local: bool = True,
    ) -> WebSocketBroadcastTask:
        """Создаёт задачу в БД и запускает её"""
        # Формируем command_payload в зависимости от типа команды
        command_payload = {}
        
        if task_data.command_type == "pi30":
            if not task_data.pi30_command:
                raise ValueError("pi30_command required for pi30 command_type")
            # Автоматически форматируем PI30 команду
            formatted_hex = format_pi30_command_with_crc_hex(task_data.pi30_command)
            command_payload = {"pi30": formatted_hex}
            # logger.info(f"📝 Formatted PI30 command '{task_data.pi30_command}' -> {formatted_hex}")
        elif task_data.command_type == "modbus_read":
            if not task_data.hex_data:
                raise ValueError("hex_data required for modbus_read command_type")
            command_payload = {
                "hex_data": task_data.hex_data,
                "command_name": task_data.command_name,
            }
        elif task_data.command_type == "modbus_tcp":
            if not task_data.command_payload:
                raise ValueError("command_payload required for modbus_tcp command_type")
            required_fields = {"ip", "port", "unit_id", "func", "start_addr", "quantity"}
            missing_fields = sorted(required_fields - set(task_data.command_payload.keys()))
            if missing_fields:
                raise ValueError(
                    "modbus_tcp command_payload missing fields: " + ", ".join(missing_fields)
                )
            command_payload = dict(task_data.command_payload)
            command_payload.setdefault("command_name", task_data.command_name)
        else:
            raise ValueError(f"Unknown command_type: {task_data.command_type}")
        
        # Создаём запись в БД
        async with async_session_maker() as db:
            new_task = WebSocketBroadcastTask(
                task_name=task_data.task_name,
                session_id=task_data.session_id,
                command_type=task_data.command_type,
                command_payload=command_payload,
                interval_ms=task_data.interval_ms,
                is_active=task_data.is_active,
                created_by=task_data.created_by
            )
            
            db.add(new_task)
            await db.commit()
            await db.refresh(new_task)
            
            # logger.info(f"💾 Created broadcast task '{new_task.task_name}' in DB (ID: {new_task.id})")
        
        # Запускаем задачу, если она активна
        if run_local and new_task.is_active:
            await self._start_task(new_task)
        
        return new_task

    async def update_task(self, task_id: str, update_data: dict, *, run_local: bool = True):
        """Обновляет параметры существующей задачи"""
        update_data = self._normalize_update_data(update_data)

        async with async_session_maker() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(WebSocketBroadcastTask).where(WebSocketBroadcastTask.id == task_id)
            )
            db_task = result.scalar_one_or_none()
            
            if not db_task:
                raise ValueError(f"Task {task_id} not found in DB")
            
            # Обновляем простые поля
            if "task_name" in update_data and update_data["task_name"] is not None:
                db_task.task_name = update_data["task_name"]
            
            if "interval_ms" in update_data and update_data["interval_ms"] is not None:
                db_task.interval_ms = update_data["interval_ms"]
            
            # Обновляем command_payload (pi30_command или hex_data)
            payload = dict(db_task.command_payload or {})
            payload_changed = False

            if "pi30_command" in update_data and update_data["pi30_command"] is not None:
                if db_task.command_type == "pi30":
                    payload["pi30"] = format_pi30_command_with_crc_hex(update_data["pi30_command"])
                    payload_changed = True
            
            if "hex_data" in update_data and update_data["hex_data"] is not None:
                if db_task.command_type == "modbus_read":
                    payload["hex_data"] = update_data["hex_data"]
                    payload_changed = True

            if "command_name" in update_data:
                if db_task.command_type == "modbus_read":
                    payload["command_name"] = update_data["command_name"]
                    payload_changed = True
                if db_task.command_type == "modbus_tcp":
                    payload["command_name"] = update_data["command_name"]
                    payload_changed = True

            if "command_payload" in update_data and update_data["command_payload"] is not None:
                if db_task.command_type == "modbus_tcp":
                    payload = dict(update_data["command_payload"])
                    payload_changed = True

            if payload_changed:
                db_task.command_payload = payload
                flag_modified(db_task, "command_payload")
            
            # Обновляем is_active
            old_is_active = db_task.is_active
            if "is_active" in update_data and update_data["is_active"] is not None:
                db_task.is_active = update_data["is_active"]
            
            await db.commit()
            await db.refresh(db_task)

            if run_local:
                if db_task.is_active != old_is_active:
                    if db_task.is_active:
                        if task_id not in self.tasks:
                            await self._start_task(db_task)
                    else:
                        await self._stop_local_task(task_id)
                elif task_id in self.tasks and db_task.is_active:
                    await self._stop_local_task(task_id)
                    await self._start_task(db_task)
            
            logger.info(f"📝 Updated broadcast task '{db_task.task_name}' (ID: {task_id})")
            return db_task

    def _cleanup_device_resources(self):
        """Очищает очереди и события для устройств без активных задач."""
        active_sessions = {info["db_task"].session_id for info in self.tasks.values()} | self.on_demand_sessions
        for session_id in set(self.device_queues.keys()) - active_sessions:
            self.device_queues.pop(session_id, None)
            self.session_activity_version.pop(session_id, None)
            self.session_activity_events.pop(session_id, None)
            self.session_bad_response_version.pop(session_id, None)
            self.session_bad_response_count.pop(session_id, None)
            self.session_bad_response_cooldown_until.pop(session_id, None)

    async def stop_and_delete(self, task_id: str, *, run_local: bool = True):
        """Останавливает задачу и удаляет из БД"""
        if run_local:
            await self._stop_local_task(task_id)
        
        # Удаляем из БД
        async with async_session_maker() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(WebSocketBroadcastTask).where(WebSocketBroadcastTask.id == task_id)
            )
            db_task = result.scalar_one_or_none()
            
            if not db_task:
                raise ValueError(f"Task {task_id} not found in DB")

            await db.delete(db_task)
            await db.commit()
            logger.info(f"🗑️ Deleted broadcast task '{db_task.task_name}' from DB")

    async def toggle_task(self, task_id: str, *, run_local: bool = True):
        """Включает/выключает задачу"""
        async with async_session_maker() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(WebSocketBroadcastTask).where(WebSocketBroadcastTask.id == task_id)
            )
            db_task = result.scalar_one_or_none()
            
            if not db_task:
                raise ValueError(f"Task {task_id} not found in DB")
            
            db_task.is_active = not db_task.is_active
            await db.commit()
            await db.refresh(db_task)

            if run_local:
                if db_task.is_active:
                    if task_id not in self.tasks:
                        await self._start_task(db_task)
                else:
                    await self._stop_local_task(task_id)
            
            return db_task

    async def disable_tasks_for_session(self, session_id: str, *, run_local: bool = True) -> list[WebSocketBroadcastTask]:
        """Выключает все broadcast-задачи для устройства по session_id."""
        async with async_session_maker() as db:
            result = await db.execute(
                select(WebSocketBroadcastTask).where(WebSocketBroadcastTask.session_id == session_id)
            )
            db_tasks = result.scalars().all()

            if not db_tasks:
                raise ValueError(f"No tasks found for session_id {session_id}")

            for db_task in db_tasks:
                db_task.is_active = False

            await db.commit()
            for db_task in db_tasks:
                await db.refresh(db_task)

        if run_local:
            for db_task in db_tasks:
                await self._stop_local_task(db_task.id)
            self._cleanup_device_resources()

        logger.info(f"Disabled {len(db_tasks)} broadcast tasks for session_id={session_id}")
        return db_tasks

    async def toggle_tasks_for_session(
        self,
        session_id: str,
        *,
        run_local: bool = True,
    ) -> tuple[list[WebSocketBroadcastTask], bool, int]:
        """Переключает все broadcast-задачи session_id как одну группу."""
        async with async_session_maker() as db:
            result = await db.execute(
                select(WebSocketBroadcastTask).where(WebSocketBroadcastTask.session_id == session_id)
            )
            db_tasks = result.scalars().all()

            if not db_tasks:
                raise ValueError(f"No tasks found for session_id {session_id}")

            target_is_active = not any(db_task.is_active for db_task in db_tasks)
            updated_count = 0

            for db_task in db_tasks:
                if db_task.is_active != target_is_active:
                    db_task.is_active = target_is_active
                    updated_count += 1

            await db.commit()
            for db_task in db_tasks:
                await db.refresh(db_task)

        if run_local:
            if target_is_active:
                for initial_delay_index, db_task in enumerate(db_tasks):
                    if db_task.id not in self.tasks:
                        await self._start_task(db_task, initial_delay_index=initial_delay_index)
            else:
                for db_task in db_tasks:
                    await self._stop_local_task(db_task.id)
                self._cleanup_device_resources()

        logger.info(
            f"Toggled {len(db_tasks)} broadcast tasks for session_id={session_id} "
            f"to {'active' if target_is_active else 'inactive'}"
        )
        return db_tasks, target_is_active, updated_count

    async def list_all(self) -> list:
        """Получает все задачи из БД с их статусом"""
        async with async_session_maker() as db:
            from sqlalchemy import select
            result = await db.execute(select(WebSocketBroadcastTask))
            db_tasks = result.scalars().all()
            
            tasks_info = []
            for db_task in db_tasks:
                local_running = db_task.id in self.tasks and not self.tasks[db_task.id]["task"].done()
                redis_running = await self._is_running_redis(db_task.id)
                is_running = bool(db_task.is_active) and (local_running or redis_running)
                payload = db_task.command_payload or {}

                pi30_command = None
                hex_data = None
                command_name = None
                command_display = None

                if db_task.command_type == "pi30":
                    pi30_command = payload.get("pi30")
                    pi30_command_name, pi30_command_description = self._decode_pi30_human_command(pi30_command)
                    if pi30_command_name and pi30_command_description:
                        command_display = f"{pi30_command_name} ({pi30_command_description})"
                    elif pi30_command_name:
                        command_display = pi30_command_name
                    else:
                        command_display = pi30_command
                elif db_task.command_type == "modbus_read":
                    hex_data = payload.get("hex_data")
                    command_name = payload.get("command_name")
                    command_display = command_name or hex_data
                    pi30_command_name = None
                    pi30_command_description = None
                elif db_task.command_type == "modbus_tcp":
                    command_name = payload.get("command_name")
                    command_display = command_name or (
                        f"{payload.get('ip')}:{payload.get('port')} "
                        f"u{payload.get('unit_id')} f{payload.get('func')} "
                        f"{payload.get('start_addr')}+{payload.get('quantity')}"
                    )
                    pi30_command_name = None
                    pi30_command_description = None
                else:
                    pi30_command_name = None
                    pi30_command_description = None

                tasks_info.append({
                    "id": db_task.id,
                    "task_name": db_task.task_name,
                    "session_id": db_task.session_id,
                    "command_type": db_task.command_type,
                    "command_payload": payload,
                    "pi30_command": pi30_command,
                    "pi30_command_name": pi30_command_name,
                    "pi30_command_description": pi30_command_description,
                    "hex_data": hex_data,
                    "command_name": command_name,
                    "command_display": command_display,
                    "interval_ms": db_task.interval_ms,
                    "is_active": db_task.is_active,
                    "is_running": is_running,
                    "created_at": db_task.created_at.isoformat() if db_task.created_at else None,
                    "created_by": db_task.created_by
                })
            
            return tasks_info
