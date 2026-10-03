import asyncio
import unittest
from types import SimpleNamespace

from backend.database.models import EnergeticDevice
from backend.database.models.energy import EnergeticObject, WebSocketBroadcastTask
from backend.services.energy import modbus_cache as modbus_cache_module
from backend.services.energy import websocket_broadcast as websocket_broadcast_module
from backend.services.energy.websocket_broadcast import BroadcastTaskManager
from backend.services.shared import websocket_events_manager as websocket_events_manager_module
from backend.services.shared.websocket_events_manager import WebSocketEventsManager


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def first(self):
        if isinstance(self._values, list):
            return self._values[0] if self._values else None
        return self._values


class _FakeExecuteResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _FakeScalars(self._values)

    def scalar_one_or_none(self):
        if isinstance(self._values, list):
            return self._values[0] if self._values else None
        return self._values

    def all(self):
        if isinstance(self._values, list):
            return self._values
        return [] if self._values is None else [self._values]


class _FakeSession:
    def __init__(self, values):
        self._values = values

    async def execute(self, _query):
        return _FakeExecuteResult(self._values)

    async def commit(self):
        return None

    async def refresh(self, _value):
        return None

    async def delete(self, value):
        if isinstance(self._values, list) and value in self._values:
            self._values.remove(value)


class _SlowFakeSession(_FakeSession):
    async def execute(self, _query):
        await asyncio.sleep(0.01)
        return _FakeExecuteResult(self._values)


class _FakeSessionContext:
    def __init__(self, values):
        self._values = values

    async def __aenter__(self):
        return _FakeSession(self._values)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSessionMaker:
    def __init__(self, values):
        self._values = values

    def __call__(self):
        return _FakeSessionContext(self._values)


class _SlowFakeSessionContext(_FakeSessionContext):
    async def __aenter__(self):
        return _SlowFakeSession(self._values)


class _SlowFakeSessionMaker(_FakeSessionMaker):
    def __call__(self):
        return _SlowFakeSessionContext(self._values)


class _SequenceFakeSession:
    def __init__(self, values_sequence):
        self._values_sequence = list(values_sequence)
        self.added = []
        self.executed = []
        self.committed = False

    async def execute(self, query):
        self.executed.append(str(query))
        values = self._values_sequence.pop(0) if self._values_sequence else []
        return _FakeExecuteResult(values)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed = True


class _SequenceFakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SequenceFakeSessionMaker:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return _SequenceFakeSessionContext(self._session)


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.published = []

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = {"value": value, "ex": ex}
        return True

    async def setex(self, key, ex, value):
        self.values[key] = {"value": value, "ex": ex}
        return True

    async def get(self, key):
        value = self.values.get(key)
        if isinstance(value, dict) and "value" in value:
            return value["value"]
        return value

    async def hget(self, key, field):
        value = self.values.get(key, {})
        if isinstance(value, dict):
            return value.get(field)
        return None

    async def hset(self, key, mapping):
        existing = self.values.get(key)
        if not isinstance(existing, dict) or "value" in existing:
            existing = {}
        existing.update(mapping)
        self.values[key] = existing
        return True

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
        return len(keys)

    async def exists(self, key):
        return int(key in self.values)

    async def rpush(self, key, value):
        self.values.setdefault(key, []).append(value)
        return len(self.values[key])

    async def lpop(self, key):
        values = self.values.get(key, [])
        return values.pop(0) if values else None

    async def expire(self, key, seconds):
        return int(key in self.values)


class BroadcastReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_demand_command_runs_without_background_tasks(self):
        manager = BroadcastTaskManager()
        session_id = "COR-MANUAL-ONLY"
        manager.on_demand_sessions.add(session_id)
        fake_redis = _FakeRedis()
        await fake_redis.set(f"ws:session:{session_id}", "connection-id")
        sent = []

        async def fake_send(session_id, event_data):
            sent.append(event_data["command_name"])
            return True

        original_redis = websocket_broadcast_module.redis_client
        original_send = websocket_broadcast_module.websocket_events_manager.send_to_session
        websocket_broadcast_module.redis_client = fake_redis
        websocket_broadcast_module.websocket_events_manager.send_to_session = fake_send
        dispatcher = None
        try:
            manager._ensure_device_dispatcher(session_id)
            dispatcher = manager.device_dispatchers[session_id]
            request_id = await websocket_broadcast_module.enqueue_on_demand_command(
                session_id, {"command_type": "pi30", "command_name": "QPIRI", "pi30": "51 0D"}
            )
            self.assertIsNotNone(request_id)
            await asyncio.sleep(0.25)
            self.assertEqual(sent, ["QPIRI"])
            manager.mark_session_activity(session_id)
        finally:
            if dispatcher:
                dispatcher.cancel()
                try:
                    await dispatcher
                except asyncio.CancelledError:
                    pass
            websocket_broadcast_module.redis_client = original_redis
            websocket_broadcast_module.websocket_events_manager.send_to_session = original_send

    async def test_on_demand_command_precedes_waiting_background_command(self):
        manager = BroadcastTaskManager()
        session_id = "COR-PRIORITY"
        manager.on_demand_sessions.add(session_id)
        fake_redis = _FakeRedis()
        await fake_redis.set(f"ws:session:{session_id}", "connection-id")
        sent = []

        async def fake_send(session_id, event_data):
            sent.append(event_data["command_name"])
            return True

        tasks = [
            WebSocketBroadcastTask(
                id=f"task-{name}", task_name=name, session_id=session_id,
                command_type="pi30", command_payload={"command_name": name},
                interval_ms=3000, is_active=True,
            )
            for name in ("first", "second")
        ]
        original_redis = websocket_broadcast_module.redis_client
        original_send = websocket_broadcast_module.websocket_events_manager.send_to_session
        websocket_broadcast_module.redis_client = fake_redis
        websocket_broadcast_module.websocket_events_manager.send_to_session = fake_send
        dispatcher = None
        try:
            for task in tasks:
                manager.tasks[task.id] = {"task": asyncio.current_task(), "db_task": task}
            queue = manager._get_device_queue(session_id)
            dispatcher = asyncio.create_task(manager._device_dispatcher(session_id))
            for task in tasks:
                queue.put_nowait({"task_id": task.id, "db_task": task, "payload": task.command_payload})

            await asyncio.sleep(0.05)
            self.assertEqual(sent, ["first"])
            request_id = await websocket_broadcast_module.enqueue_on_demand_command(
                session_id, {"command_type": "pi30", "command_name": "manual", "pi30": "51 0D"}
            )
            self.assertIsNotNone(request_id)
            manager.mark_session_activity(session_id)
            await asyncio.sleep(0.05)
            self.assertEqual(sent, ["first", "manual"])
            manager.mark_session_activity(session_id)
            await asyncio.sleep(0.05)
            self.assertEqual(sent, ["first", "manual", "second"])
            manager.mark_session_activity(session_id)
        finally:
            if dispatcher:
                dispatcher.cancel()
                try:
                    await dispatcher
                except asyncio.CancelledError:
                    pass
            websocket_broadcast_module.redis_client = original_redis
            websocket_broadcast_module.websocket_events_manager.send_to_session = original_send

    def test_good_device_response_detection_rejects_rs485_no_response(self):
        payload = {
            "device_id": "COR-08D1F99A1AD0",
            "data": {
                "cmd": "QPIGS",
                "hex_response": "No response from RS485",
            },
        }

        self.assertFalse(BroadcastTaskManager.is_good_device_response(payload))

    def test_good_device_response_detection_rejects_pi30_nak(self):
        payload = {
            "device_id": "COR-08D1F99A1AD0",
            "data": {
                "cmd": "QPIGS",
                "hex_response": "284e414b0d",
            },
        }

        self.assertFalse(BroadcastTaskManager.is_good_device_response(payload))

    def test_good_device_response_detection_accepts_regular_response(self):
        payload = {
            "device_id": "COR-08D1F99A1AD0",
            "data": {
                "cmd": "QPIGS",
                "hex_response": "283232352E342034392E3920",
            },
        }

        self.assertTrue(BroadcastTaskManager.is_good_device_response(payload))

    def test_bad_response_cooldown_grows_and_resets_after_good_response(self):
        manager = BroadcastTaskManager()
        session_id = "device-adaptive"

        first_cooldown = manager.mark_session_bad_response(session_id)
        second_cooldown = manager.mark_session_bad_response(session_id)

        self.assertEqual(first_cooldown, 8.0)
        self.assertEqual(second_cooldown, 10.0)
        self.assertEqual(manager.session_bad_response_count[session_id], 2)

        manager.mark_session_activity(session_id)

        self.assertNotIn(session_id, manager.session_bad_response_count)
        self.assertNotIn(session_id, manager.session_bad_response_cooldown_until)
        self.assertEqual(manager.mark_session_bad_response(session_id), 8.0)

    def test_debug_bypass_session_ignores_bad_response_cooldown(self):
        manager = BroadcastTaskManager()
        session_id = "COR-08D1F99A1AD0"

        self.assertTrue(manager._bypass_response_gate_for_session(session_id))
        self.assertEqual(manager.mark_session_bad_response(session_id), 0.0)
        self.assertNotIn(session_id, manager.session_bad_response_count)
        self.assertNotIn(session_id, manager.session_bad_response_cooldown_until)

    async def test_wait_for_session_response_returns_bad_response(self):
        manager = BroadcastTaskManager()
        session_id = "device-bad-wait"
        previous_activity_version = manager.session_activity_version.get(session_id, 0)
        previous_bad_response_version = manager.session_bad_response_version.get(session_id, 0)

        async def notify():
            await asyncio.sleep(0.01)
            manager.mark_session_bad_response(session_id)

        notifier = asyncio.create_task(notify())
        try:
            result = await manager._wait_for_session_response(
                session_id=session_id,
                previous_activity_version=previous_activity_version,
                previous_bad_response_version=previous_bad_response_version,
                timeout_seconds=0.5,
            )
        finally:
            await notifier

        self.assertEqual(result, "bad_response")

    async def test_cor_agent_snapshot_cache_keeps_latest_events_by_command(self):
        fake_redis = _FakeRedis()
        original_redis = modbus_cache_module.redis_client
        modbus_cache_module.redis_client = fake_redis

        try:
            await modbus_cache_module.set_cor_agent_snapshot_cache(
                "COR-8C4B14DDBDD0",
                {
                    "device_id": "COR-8C4B14DDBDD0",
                    "data": {"cmd": "modbus_read", "command_name": "grid", "hex_response": "AA"},
                    "timestamp": "2026-04-16T13:21:08.725230",
                },
            )
            await modbus_cache_module.set_cor_agent_snapshot_cache(
                "COR-8C4B14DDBDD0",
                {
                    "device_id": "COR-8C4B14DDBDD0",
                    "data": {"cmd": "modbus_read", "command_name": "PV1-4", "hex_response": "BB"},
                    "timestamp": "2026-04-16T13:15:01.992147",
                },
            )
            snapshot = await modbus_cache_module.get_cor_agent_snapshot_cache("COR-8C4B14DDBDD0")
        finally:
            modbus_cache_module.redis_client = original_redis

        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["device_id"], "COR-8C4B14DDBDD0")
        self.assertEqual(set(snapshot["events"].keys()), {"grid", "PV1-4"})

    async def test_cor_agent_snapshot_cache_prefers_pi30_cmd_when_command_name_unknown(self):
        fake_redis = _FakeRedis()
        original_redis = modbus_cache_module.redis_client
        modbus_cache_module.redis_client = fake_redis

        try:
            await modbus_cache_module.set_cor_agent_snapshot_cache(
                "COR-70B8F662B424",
                {
                    "device_id": "COR-70B8F662B424",
                    "data": {"cmd": "QPIGS", "command_name": "UNKNOWN", "hex_response": "AA"},
                    "timestamp": "2026-04-17T09:14:42.355622",
                },
            )
            await modbus_cache_module.set_cor_agent_snapshot_cache(
                "COR-70B8F662B424",
                {
                    "device_id": "COR-70B8F662B424",
                    "data": {"cmd": "QMOD", "command_name": "UNKNOWN", "hex_response": "BB"},
                    "timestamp": "2026-04-17T09:14:43.355622",
                },
            )
            snapshot = await modbus_cache_module.get_cor_agent_snapshot_cache("COR-70B8F662B424")
        finally:
            modbus_cache_module.redis_client = original_redis

        self.assertTrue(snapshot["ok"])
        self.assertEqual(set(snapshot["events"].keys()), {"QPIGS", "QMOD"})

    async def test_send_to_session_resolves_legacy_device_uuid(self):
        manager = WebSocketEventsManager(worker_id="worker-test")

        fake_redis = _FakeRedis()
        await fake_redis.set("ws:session:COR-70B8F66247DC", "conn-1")
        await fake_redis.hset("ws:connection:conn-1", {"worker_id": "worker-test"})

        legacy_device = EnergeticDevice(
            id="6492e15b-ab2e-4f60-b8f0-d85adf9b40d0",
            device_id="COR-70B8F66247DC",
            owner_cor_id="owner-cor-id",
            name="Bridge",
        )

        original_redis = websocket_events_manager_module.redis_client
        original_session_maker = websocket_events_manager_module.async_session_maker
        websocket_events_manager_module.redis_client = fake_redis
        websocket_events_manager_module.async_session_maker = _FakeSessionMaker([legacy_device])

        try:
            sent = await manager.send_to_session(
                "6492e15b-ab2e-4f60-b8f0-d85adf9b40d0",
                {"hex_data": "01 03"},
            )
        finally:
            websocket_events_manager_module.redis_client = original_redis
            websocket_events_manager_module.async_session_maker = original_session_maker

        self.assertTrue(sent)
        self.assertEqual(fake_redis.values["ws:session:6492e15b-ab2e-4f60-b8f0-d85adf9b40d0"]["value"], "conn-1")
        self.assertEqual(len(fake_redis.published), 1)

    async def test_reload_from_db_stops_removed_and_restarts_changed_tasks(self):
        manager = BroadcastTaskManager()

        local_changed = WebSocketBroadcastTask(
            id="task-changed",
            task_name="Old name",
            session_id="device-1",
            command_type="modbus_read",
            command_payload={"hex_data": "01 03"},
            interval_ms=5000,
            is_active=True,
        )
        local_removed = WebSocketBroadcastTask(
            id="task-removed",
            task_name="Removed",
            session_id="device-2",
            command_type="modbus_read",
            command_payload={"hex_data": "02 03"},
            interval_ms=10000,
            is_active=True,
        )
        db_changed = WebSocketBroadcastTask(
            id="task-changed",
            task_name="New name",
            session_id="device-1",
            command_type="modbus_read",
            command_payload={"hex_data": "01 03"},
            interval_ms=15000,
            is_active=True,
        )
        db_new = WebSocketBroadcastTask(
            id="task-new",
            task_name="New task",
            session_id="device-3",
            command_type="modbus_read",
            command_payload={"hex_data": "03 03"},
            interval_ms=20000,
            is_active=True,
        )

        manager.tasks = {
            "task-changed": {"task": object(), "db_task": local_changed},
            "task-removed": {"task": object(), "db_task": local_removed},
        }

        started = []
        stopped = []

        async def fake_start(db_task, initial_delay_index=0):
            started.append(db_task.id)
            manager.tasks[db_task.id] = {"task": object(), "db_task": db_task}

        async def fake_stop(task_id, *, clear_running=True):
            stopped.append(task_id)
            manager.tasks.pop(task_id, None)

        original_session_maker = websocket_broadcast_module.async_session_maker
        manager._start_task = fake_start
        manager._stop_local_task = fake_stop
        websocket_broadcast_module.async_session_maker = _FakeSessionMaker([db_changed, db_new])

        try:
            await manager.reload_from_db()
        finally:
            websocket_broadcast_module.async_session_maker = original_session_maker

        self.assertEqual(stopped, ["task-removed", "task-changed"])
        self.assertEqual(started, ["task-changed", "task-new"])
        self.assertEqual(set(manager.tasks.keys()), {"task-changed", "task-new"})
        self.assertEqual(manager.tasks["task-changed"]["db_task"].interval_ms, 15000)

    async def test_reload_from_db_is_serialized_and_does_not_double_start_task(self):
        manager = BroadcastTaskManager()

        db_task = WebSocketBroadcastTask(
            id="task-single",
            task_name="Single",
            session_id="device-1",
            command_type="modbus_read",
            command_payload={"hex_data": "01 03"},
            interval_ms=15000,
            is_active=True,
        )

        start_calls = []

        async def fake_start(task, initial_delay_index=0):
            start_calls.append(task.id)
            manager.tasks[task.id] = {"task": object(), "db_task": task}

        original_session_maker = websocket_broadcast_module.async_session_maker
        manager._start_task = fake_start
        websocket_broadcast_module.async_session_maker = _SlowFakeSessionMaker([db_task])

        try:
            await asyncio.gather(manager.reload_from_db(), manager.reload_from_db())
        finally:
            websocket_broadcast_module.async_session_maker = original_session_maker

        self.assertEqual(start_calls, ["task-single"])
        self.assertIn("task-single", manager.tasks)

    async def test_sync_bridge_tasks_replaces_stale_object_preset_for_same_bridge(self):
        manager = BroadcastTaskManager()
        obj = EnergeticObject(
            id="object-new",
            name="New Object",
            model_name="SUN-30K-SG02HP3-EU",
            vendor="Deye",
            protocol="cor_bridge",
            cor_bridges=["bridge-db-id"],
            slave_ids=[1],
            owner_cor_id="owner-cor-id",
            is_active=True,
        )
        bridge = EnergeticDevice(
            id="bridge-db-id",
            device_id="COR-70B8F66247DC",
            owner_cor_id="owner-cor-id",
            name="Bridge",
        )
        old_task = SimpleNamespace(
            id="old-task",
            session_id="COR-70B8F66247DC",
            task_name="Old Object:COR-70B8F66247DC:grid",
            is_active=False,
        )
        fake_session = _SequenceFakeSession(
            [
                [obj],
                [bridge],
                ["COR-70B8F66247DC"],
                [old_task],
                [],
            ]
        )

        original_session_maker = websocket_broadcast_module.async_session_maker
        websocket_broadcast_module.async_session_maker = _SequenceFakeSessionMaker(fake_session)

        try:
            await manager._sync_bridge_tasks_with_presets()
        finally:
            websocket_broadcast_module.async_session_maker = original_session_maker

        self.assertTrue(fake_session.committed)
        self.assertTrue(
            any("DELETE FROM websocket_broadcast_tasks" in sql for sql in fake_session.executed),
            msg="Expected stale preset-managed websocket broadcast tasks to be deleted",
        )
        self.assertEqual(len(fake_session.added), 8)
        self.assertTrue(
            all(task.task_name.startswith("New Object:COR-70B8F66247DC:") for task in fake_session.added)
        )
        self.assertTrue(all(task.session_id == "COR-70B8F66247DC" for task in fake_session.added))
        self.assertEqual(
            {task.command_payload["command_name"] for task in fake_session.added},
            {"grid", "batt", "PV1-4", "PV5-8", "load", "gen", "service", "settings"},
        )

    async def test_sync_bridge_tasks_keeps_inactive_object_preset_tasks_inactive(self):
        manager = BroadcastTaskManager()
        obj = EnergeticObject(
            id="object-inactive",
            name="Inactive COR Object",
            model_name="FW-001-20Kw-Q",
            vendor="COR",
            protocol="cor_bridge",
            cor_bridges=["COR-8C4B14DF4C24"],
            slave_ids=[1],
            owner_cor_id="owner-cor-id",
            is_active=False,
        )
        fake_session = _SequenceFakeSession(
            [
                [obj],
                [],
                ["COR-8C4B14DF4C24"],
                [],
                [],
            ]
        )

        original_session_maker = websocket_broadcast_module.async_session_maker
        websocket_broadcast_module.async_session_maker = _SequenceFakeSessionMaker(fake_session)

        try:
            await manager._sync_bridge_tasks_with_presets()
        finally:
            websocket_broadcast_module.async_session_maker = original_session_maker

        self.assertTrue(fake_session.committed)
        self.assertEqual(len(fake_session.added), 4)
        self.assertTrue(all(task.session_id == "COR-8C4B14DF4C24" for task in fake_session.added))
        self.assertTrue(all(task.command_type == "pi30" for task in fake_session.added))
        self.assertTrue(all(task.is_active is False for task in fake_session.added))
        self.assertEqual(
            {task.command_payload["command_name"] for task in fake_session.added},
            {"fw_dc_status", "fw_motor_status", "fw_generator_status", "fw_status"},
        )

    async def test_acquire_send_slot_blocks_duplicate_send_across_managers(self):
        manager_a = BroadcastTaskManager()
        manager_b = BroadcastTaskManager()

        db_task = WebSocketBroadcastTask(
            id="task-slot",
            task_name="Single",
            session_id="device-1",
            command_type="modbus_read",
            command_payload={"hex_data": "01 03"},
            interval_ms=15000,
            is_active=True,
        )

        fake_redis = _FakeRedis()
        original_redis = websocket_broadcast_module.redis_client
        websocket_broadcast_module.redis_client = fake_redis

        try:
            first = await manager_a._acquire_send_slot(db_task, now_ts=1000.0)
            second = await manager_b._acquire_send_slot(db_task, now_ts=1000.42)
        finally:
            websocket_broadcast_module.redis_client = original_redis

        self.assertTrue(first)
        self.assertFalse(second)

    async def test_wait_for_session_activity_returns_true_after_mark(self):
        manager = BroadcastTaskManager()
        session_id = "device-1"
        previous_version = manager.session_activity_version.get(session_id, 0)

        async def notify():
            await asyncio.sleep(0.01)
            manager.mark_session_activity(session_id)

        notifier = asyncio.create_task(notify())
        try:
            result = await manager._wait_for_session_activity(session_id, previous_version, 0.5)
        finally:
            await notifier

        self.assertTrue(result)

    async def test_wait_for_session_activity_times_out_without_mark(self):
        manager = BroadcastTaskManager()
        result = await manager._wait_for_session_activity("device-1", 0, 0.01)
        self.assertFalse(result)

    async def test_wait_for_session_activity_ignores_stale_event_and_waits_new_mark(self):
        manager = BroadcastTaskManager()
        session_id = "device-1"

        # Старая активность уже была, event установлен.
        manager.mark_session_activity(session_id)
        previous_version = manager.session_activity_version.get(session_id, 0)

        async def notify_new_activity():
            await asyncio.sleep(0.02)
            manager.mark_session_activity(session_id)

        notifier = asyncio.create_task(notify_new_activity())
        try:
            result = await manager._wait_for_session_activity(session_id, previous_version, 0.2)
        finally:
            await notifier

        self.assertTrue(result)

    async def test_toggle_tasks_for_session_disables_group_when_any_task_active(self):
        manager = BroadcastTaskManager()
        db_tasks = [
            WebSocketBroadcastTask(
                id="task-active",
                task_name="Active",
                session_id="device-1",
                command_type="modbus_read",
                command_payload={"hex_data": "01 03"},
                interval_ms=5000,
                is_active=True,
            ),
            WebSocketBroadcastTask(
                id="task-inactive",
                task_name="Inactive",
                session_id="device-1",
                command_type="modbus_read",
                command_payload={"hex_data": "02 03"},
                interval_ms=5000,
                is_active=False,
            ),
        ]
        stopped = []

        async def fake_stop(task_id, *, clear_running=True):
            stopped.append(task_id)

        original_session_maker = websocket_broadcast_module.async_session_maker
        manager._stop_local_task = fake_stop
        websocket_broadcast_module.async_session_maker = _FakeSessionMaker(db_tasks)

        try:
            tasks, is_active, updated_count = await manager.toggle_tasks_for_session("device-1")
        finally:
            websocket_broadcast_module.async_session_maker = original_session_maker

        self.assertFalse(is_active)
        self.assertEqual(updated_count, 1)
        self.assertTrue(all(not task.is_active for task in tasks))
        self.assertEqual(stopped, ["task-active", "task-inactive"])

    async def test_toggle_tasks_for_session_enables_group_when_all_tasks_inactive(self):
        manager = BroadcastTaskManager()
        db_tasks = [
            WebSocketBroadcastTask(
                id="task-a",
                task_name="Task A",
                session_id="device-1",
                command_type="modbus_read",
                command_payload={"hex_data": "01 03"},
                interval_ms=5000,
                is_active=False,
            ),
            WebSocketBroadcastTask(
                id="task-b",
                task_name="Task B",
                session_id="device-1",
                command_type="modbus_read",
                command_payload={"hex_data": "02 03"},
                interval_ms=5000,
                is_active=False,
            ),
        ]
        started = []

        async def fake_start(db_task, initial_delay_index=0):
            started.append((db_task.id, initial_delay_index))
            manager.tasks[db_task.id] = {"task": object(), "db_task": db_task}

        original_session_maker = websocket_broadcast_module.async_session_maker
        manager._start_task = fake_start
        websocket_broadcast_module.async_session_maker = _FakeSessionMaker(db_tasks)

        try:
            tasks, is_active, updated_count = await manager.toggle_tasks_for_session("device-1")
        finally:
            websocket_broadcast_module.async_session_maker = original_session_maker

        self.assertTrue(is_active)
        self.assertEqual(updated_count, 2)
        self.assertTrue(all(task.is_active for task in tasks))
        self.assertEqual(started, [("task-a", 0), ("task-b", 1)])

    async def test_list_all_does_not_report_inactive_task_as_running(self):
        manager = BroadcastTaskManager()
        db_task = WebSocketBroadcastTask(
            id="task-inactive-running-heartbeat",
            task_name="Inactive with heartbeat",
            session_id="device-1",
            command_type="modbus_read",
            command_payload={"hex_data": "01 03"},
            interval_ms=5000,
            is_active=False,
        )

        fake_redis = _FakeRedis()
        await fake_redis.set(manager._running_heartbeat_key(db_task.id), "1")

        original_session_maker = websocket_broadcast_module.async_session_maker
        original_redis = websocket_broadcast_module.redis_client
        websocket_broadcast_module.async_session_maker = _FakeSessionMaker([db_task])
        websocket_broadcast_module.redis_client = fake_redis

        try:
            tasks = await manager.list_all()
        finally:
            websocket_broadcast_module.async_session_maker = original_session_maker
            websocket_broadcast_module.redis_client = original_redis

        self.assertEqual(len(tasks), 1)
        self.assertFalse(tasks[0]["is_active"])
        self.assertFalse(tasks[0]["is_running"])

    async def test_staggered_startup_delays_multiple_new_tasks_on_same_device(self):
        """Test that multiple new tasks on one device start with 1-second intervals."""
        manager = BroadcastTaskManager()

        # Three tasks for same device
        tasks_data = [
            WebSocketBroadcastTask(
                id=f"task-{i}",
                task_name=f"Task {i}",
                session_id="device-1",
                command_type="modbus_read",
                command_payload={"hex_data": "01 03"},
                interval_ms=10000,
                is_active=True,
            )
            for i in range(3)
        ]

        start_times = {}

        # Mock _start_task to track initial_delay_index for each task
        original_start_task = manager._start_task

        async def fake_start_task(db_task, initial_delay_index=0):
            start_times[db_task.id] = initial_delay_index
            manager.tasks[db_task.id] = {"task": asyncio.Event(), "db_task": db_task}

        manager._start_task = fake_start_task

        # Mock async_session_maker
        original_session_maker = websocket_broadcast_module.async_session_maker
        websocket_broadcast_module.async_session_maker = _FakeSessionMaker(tasks_data)

        try:
            await manager.reload_from_db()
        finally:
            websocket_broadcast_module.async_session_maker = original_session_maker

        # Verify each task got consecutive initial_delay_index
        self.assertEqual(len(start_times), 3)
        indices = sorted(start_times.values())
        self.assertEqual(indices, [0, 1, 2])

    async def test_device_dispatcher_sends_one_at_a_time_and_waits_for_response(self):
        """Диспетчер отправляет команды по одной, следующая идёт только после ответа."""
        manager = BroadcastTaskManager()
        session_id = "device-dispatch"

        db_task_a = WebSocketBroadcastTask(
            id="task-a",
            task_name="Task A",
            session_id=session_id,
            command_type="modbus_read",
            command_payload={"hex_data": "01 03"},
            interval_ms=5000,
            is_active=True,
        )
        db_task_b = WebSocketBroadcastTask(
            id="task-b",
            task_name="Task B",
            session_id=session_id,
            command_type="modbus_read",
            command_payload={"hex_data": "02 03"},
            interval_ms=3000,
            is_active=True,
        )

        send_log = []

        # Мокируем send_to_session и Redis-локи
        async def fake_send(session_id, event_data):
            send_log.append(("send", event_data["hex_data"]))
            return True

        original_send = websocket_broadcast_module.websocket_events_manager.send_to_session
        websocket_broadcast_module.websocket_events_manager.send_to_session = fake_send

        fake_redis = _FakeRedis()
        original_redis = websocket_broadcast_module.redis_client
        websocket_broadcast_module.redis_client = fake_redis

        try:
            # Регистрируем задачи
            manager.tasks["task-a"] = {"task": asyncio.current_task(), "db_task": db_task_a}
            manager.tasks["task-b"] = {"task": asyncio.current_task(), "db_task": db_task_b}

            queue = manager._get_device_queue(session_id)
            dispatcher_task = asyncio.create_task(manager._device_dispatcher(session_id))

            # Ставим 2 команды в очередь
            queue.put_nowait({"task_id": "task-a", "db_task": db_task_a, "payload": {"hex_data": "01 03"}})
            queue.put_nowait({"task_id": "task-b", "db_task": db_task_b, "payload": {"hex_data": "02 03"}})

            # Ждём немного и проверяем: должна уйти только ПЕРВАЯ команда
            await asyncio.sleep(0.02)
            self.assertEqual(len(send_log), 1, "До ответа должна уйти только 1 команда")
            self.assertEqual(send_log[0][1], "01 03")

            # Симулируем ответ устройства
            manager.mark_session_activity(session_id)
            await asyncio.sleep(0.02)

            # Теперь должна уйти вторая команда
            self.assertEqual(len(send_log), 2, "После ответа - следующая команда")
            self.assertEqual(send_log[1][1], "02 03")

            # Симулируем ответ и даём диспетчеру завершить работу
            manager.mark_session_activity(session_id)
            dispatcher_task.cancel()
            try:
                await dispatcher_task
            except (asyncio.CancelledError, Exception):
                pass
        finally:
            websocket_broadcast_module.websocket_events_manager.send_to_session = original_send
            websocket_broadcast_module.redis_client = original_redis

    async def test_device_dispatcher_applies_bad_response_cooldown_before_next_command(self):
        manager = BroadcastTaskManager()
        manager.bad_response_cooldown_base_seconds = 0.05
        manager.bad_response_cooldown_step_seconds = 0.02
        manager.bad_response_cooldown_max_seconds = 0.1
        session_id = "device-cooldown"

        db_task_a = WebSocketBroadcastTask(
            id="task-a",
            task_name="Task A",
            session_id=session_id,
            command_type="modbus_read",
            command_payload={"hex_data": "01 03"},
            interval_ms=5000,
            is_active=True,
        )
        db_task_b = WebSocketBroadcastTask(
            id="task-b",
            task_name="Task B",
            session_id=session_id,
            command_type="modbus_read",
            command_payload={"hex_data": "02 03"},
            interval_ms=5000,
            is_active=True,
        )

        send_log = []

        async def fake_send(session_id, event_data):
            send_log.append(event_data["hex_data"])
            return True

        original_send = websocket_broadcast_module.websocket_events_manager.send_to_session
        websocket_broadcast_module.websocket_events_manager.send_to_session = fake_send

        fake_redis = _FakeRedis()
        original_redis = websocket_broadcast_module.redis_client
        websocket_broadcast_module.redis_client = fake_redis

        try:
            manager.tasks["task-a"] = {"task": asyncio.current_task(), "db_task": db_task_a}
            manager.tasks["task-b"] = {"task": asyncio.current_task(), "db_task": db_task_b}

            queue = manager._get_device_queue(session_id)
            dispatcher_task = asyncio.create_task(manager._device_dispatcher(session_id))

            queue.put_nowait({"task_id": "task-a", "db_task": db_task_a, "payload": {"hex_data": "01 03"}})
            queue.put_nowait({"task_id": "task-b", "db_task": db_task_b, "payload": {"hex_data": "02 03"}})

            await asyncio.sleep(0.02)
            self.assertEqual(send_log, ["01 03"])

            manager.mark_session_bad_response(session_id)
            await asyncio.sleep(0.02)
            self.assertEqual(send_log, ["01 03"])

            await asyncio.sleep(0.05)
            self.assertEqual(send_log, ["01 03", "02 03"])

            manager.mark_session_activity(session_id)
            dispatcher_task.cancel()
            try:
                await dispatcher_task
            except (asyncio.CancelledError, Exception):
                pass
        finally:
            websocket_broadcast_module.websocket_events_manager.send_to_session = original_send
            websocket_broadcast_module.redis_client = original_redis

    async def test_debug_bypass_session_dispatcher_does_not_wait_for_response(self):
        manager = BroadcastTaskManager()
        session_id = "COR-08D1F99A1AD0"

        db_task_a = WebSocketBroadcastTask(
            id="task-a",
            task_name="Task A",
            session_id=session_id,
            command_type="modbus_read",
            command_payload={"hex_data": "01 03"},
            interval_ms=5000,
            is_active=True,
        )
        db_task_b = WebSocketBroadcastTask(
            id="task-b",
            task_name="Task B",
            session_id=session_id,
            command_type="modbus_read",
            command_payload={"hex_data": "02 03"},
            interval_ms=5000,
            is_active=True,
        )

        send_log = []

        async def fake_send(session_id, event_data):
            send_log.append(event_data["hex_data"])
            return True

        async def always_active(_task_id):
            return True

        original_send = websocket_broadcast_module.websocket_events_manager.send_to_session
        websocket_broadcast_module.websocket_events_manager.send_to_session = fake_send
        manager._is_task_active_in_db = always_active

        fake_redis = _FakeRedis()
        original_redis = websocket_broadcast_module.redis_client
        websocket_broadcast_module.redis_client = fake_redis

        try:
            manager.tasks["task-a"] = {"task": asyncio.current_task(), "db_task": db_task_a}
            manager.tasks["task-b"] = {"task": asyncio.current_task(), "db_task": db_task_b}

            queue = manager._get_device_queue(session_id)
            dispatcher_task = asyncio.create_task(manager._device_dispatcher(session_id))

            queue.put_nowait({"task_id": "task-a", "db_task": db_task_a, "payload": {"hex_data": "01 03"}})
            queue.put_nowait({"task_id": "task-b", "db_task": db_task_b, "payload": {"hex_data": "02 03"}})

            await asyncio.sleep(0.02)

            self.assertEqual(send_log, ["01 03", "02 03"])
            self.assertNotIn(session_id, manager.session_bad_response_count)
            dispatcher_task.cancel()
            try:
                await dispatcher_task
            except (asyncio.CancelledError, Exception):
                pass
        finally:
            websocket_broadcast_module.websocket_events_manager.send_to_session = original_send
            websocket_broadcast_module.redis_client = original_redis

    async def test_device_dispatcher_skips_removed_task(self):
        """Диспетчер пропускает команды задач которые были удалены."""
        manager = BroadcastTaskManager()
        session_id = "device-skip"

        db_task = WebSocketBroadcastTask(
            id="task-deleted",
            task_name="Will be deleted",
            session_id=session_id,
            command_type="modbus_read",
            command_payload={"hex_data": "01 03"},
            interval_ms=5000,
            is_active=True,
        )

        send_log = []

        async def fake_send(session_id, event_data):
            send_log.append(event_data)
            return True

        original_send = websocket_broadcast_module.websocket_events_manager.send_to_session
        websocket_broadcast_module.websocket_events_manager.send_to_session = fake_send
        fake_redis = _FakeRedis()
        original_redis = websocket_broadcast_module.redis_client
        websocket_broadcast_module.redis_client = fake_redis

        try:
            # Задача НЕ в self.tasks (уже удалена)
            queue = manager._get_device_queue(session_id)
            dispatcher_task = asyncio.create_task(manager._device_dispatcher(session_id))

            # Ставим команду удалённой задачи в очередь
            queue.put_nowait({"task_id": "task-deleted", "db_task": db_task, "payload": {"hex_data": "01 03"}})

            await asyncio.sleep(0.05)

            # Команда не должна быть отправлена
            self.assertEqual(send_log, [], "Удалённая задача не должна отправлять команды")

            dispatcher_task.cancel()
            try:
                await dispatcher_task
            except (asyncio.CancelledError, Exception):
                pass
        finally:
            websocket_broadcast_module.websocket_events_manager.send_to_session = original_send
            websocket_broadcast_module.redis_client = original_redis

    async def test_device_dispatcher_skips_task_deactivated_in_db(self):
        """Диспетчер не отправляет команду, если runtime-задача устарела и уже выключена в БД."""
        manager = BroadcastTaskManager()
        session_id = "device-stale-inactive"

        stale_task = WebSocketBroadcastTask(
            id="task-stale-inactive",
            task_name="Stale inactive",
            session_id=session_id,
            command_type="pi30",
            command_payload={"pi30": "51 50 49 47 53 B7 A9 0D"},
            interval_ms=3000,
            is_active=True,
        )

        send_log = []

        async def fake_send(session_id, event_data):
            send_log.append(event_data)
            return True

        original_send = websocket_broadcast_module.websocket_events_manager.send_to_session
        original_session_maker = websocket_broadcast_module.async_session_maker
        fake_redis = _FakeRedis()
        original_redis = websocket_broadcast_module.redis_client
        websocket_broadcast_module.websocket_events_manager.send_to_session = fake_send
        websocket_broadcast_module.async_session_maker = _FakeSessionMaker([False])
        websocket_broadcast_module.redis_client = fake_redis

        try:
            manager.tasks[stale_task.id] = {"task": object(), "db_task": stale_task}
            queue = manager._get_device_queue(session_id)
            dispatcher_task = asyncio.create_task(manager._device_dispatcher(session_id))
            queue.put_nowait({
                "task_id": stale_task.id,
                "db_task": stale_task,
                "payload": stale_task.command_payload,
            })

            await asyncio.sleep(0.02)

            self.assertEqual(send_log, [])
            self.assertNotIn(stale_task.id, manager.tasks)

            dispatcher_task.cancel()
            try:
                await dispatcher_task
            except (asyncio.CancelledError, Exception):
                pass
        finally:
            websocket_broadcast_module.websocket_events_manager.send_to_session = original_send
            websocket_broadcast_module.async_session_maker = original_session_maker
            websocket_broadcast_module.redis_client = original_redis

    async def test_device_send_lock_serializes_across_processes(self):
        """Redis device send lock не даёт двум диспетчерам отправлять одновременно."""
        manager_a = BroadcastTaskManager()
        manager_b = BroadcastTaskManager()
        session_id = "device-lock"

        fake_redis = _FakeRedis()
        original_redis = websocket_broadcast_module.redis_client
        websocket_broadcast_module.redis_client = fake_redis

        try:
            # Process A захватывает lock
            acquired_a = await manager_a._acquire_device_send_lock(session_id)
            # Process B не может захватить пока A держит
            acquired_b = await manager_b._acquire_device_send_lock(session_id)

            self.assertTrue(acquired_a)
            self.assertFalse(acquired_b)

            # После освобождения - B захватывает
            await manager_a._release_device_send_lock(session_id)
            acquired_b_after = await manager_b._acquire_device_send_lock(session_id)
            self.assertTrue(acquired_b_after)
        finally:
            websocket_broadcast_module.redis_client = original_redis


if __name__ == "__main__":
    unittest.main()
