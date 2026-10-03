import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.database.models.energy import (
    DevicePollingTask,
    EnergeticDevice,
    EnergeticObject,
    WebSocketBroadcastTask,
)
from backend.routes.energy.polling_tasks import get_object_combined_polling_tasks


class _FakeScalars:
    def __init__(self, value):
        self.value = value

    def all(self):
        if self.value is None:
            return []
        return self.value if isinstance(self.value, list) else [self.value]


class _FakeExecuteResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return _FakeScalars(self.value)


class _FakeAsyncSession:
    def __init__(self, *, devices=None, object_tasks=None, bridge_tasks=None):
        self.devices = devices or []
        self.object_tasks = object_tasks or []
        self.bridge_tasks = bridge_tasks or []

    async def execute(self, query):
        sql = str(query)
        if "energetic_devices" in sql:
            return _FakeExecuteResult(self.devices)
        if "device_polling_tasks" in sql:
            return _FakeExecuteResult(self.object_tasks)
        if "websocket_broadcast_tasks" in sql:
            return _FakeExecuteResult(self.bridge_tasks)
        return _FakeExecuteResult([])


class CombinedPollingTasksTests(unittest.IsolatedAsyncioTestCase):
    async def test_combined_route_returns_object_and_cor_bridge_tasks(self):
        created_at = datetime(2026, 9, 11, 12, 0, 0)
        energetic_object = EnergeticObject(
            id="object-id",
            name="Мой дом",
            cor_bridges=["14785b6c-ddac-4d2e-8515-9c4a3c846334"],
        )
        bridge = EnergeticDevice(
            id="14785b6c-ddac-4d2e-8515-9c4a3c846334",
            device_id="COR-08D1F99A1AD0",
            owner_cor_id="owner-cor-id",
        )
        object_task = DevicePollingTask(
            id="polling-task-id",
            energetic_object_id="object-id",
            task_type="modbus_registers",
            command_config={"register_groups": ["load"]},
            interval_ms=15000,
            is_active=True,
            created_at=created_at,
            updated_at=created_at,
        )
        bridge_task = WebSocketBroadcastTask(
            id="broadcast-task-id",
            task_name="Мой дом:COR-08D1F99A1AD0:QPIGS",
            session_id="COR-08D1F99A1AD0",
            command_type="pi30",
            command_payload={
                "pi30": "51 50 49 47 53 B7 A9 0D",
                "command_name": "QPIGS",
            },
            interval_ms=3000,
            is_active=False,
            created_at=created_at,
            updated_at=created_at,
            created_by="owner-cor-id",
        )
        db = _FakeAsyncSession(
            devices=[bridge],
            object_tasks=[object_task],
            bridge_tasks=[bridge_task],
        )

        with patch(
            "backend.routes.energy.polling_tasks.ensure_object_permission",
            new=AsyncMock(return_value=energetic_object),
        ):
            response = await get_object_combined_polling_tasks(
                "object-id",
                db=db,
                current_user=SimpleNamespace(cor_id="owner-cor-id"),
                _=SimpleNamespace(),
            )

        self.assertEqual(response.energetic_object_id, "object-id")
        self.assertEqual(response.energetic_object_name, "Мой дом")
        self.assertEqual(
            response.bridge_session_ids,
            [
                "14785b6c-ddac-4d2e-8515-9c4a3c846334",
                "COR-08D1F99A1AD0",
            ],
        )
        self.assertEqual(response.object_total_tasks, 1)
        self.assertEqual(response.object_active_tasks, 1)
        self.assertEqual(response.bridge_total_tasks, 1)
        self.assertEqual(response.bridge_active_tasks, 0)
        self.assertEqual(response.total_tasks, 2)
        self.assertEqual(response.active_tasks, 1)
        self.assertEqual(response.object_tasks[0].id, "polling-task-id")
        self.assertEqual(response.bridge_tasks[0].id, "broadcast-task-id")
        self.assertEqual(response.bridge_tasks[0].session_id, "COR-08D1F99A1AD0")


if __name__ == "__main__":
    unittest.main()
