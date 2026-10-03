import unittest
from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

from backend.database.models.energy import DevicePollingTask, EnergeticObject, WebSocketBroadcastTask
from backend.database.models import EnergeticDevice
from backend.repository.energy.cerbo_service import create_energetic_object, delete_energetic_object, update_energetic_object
from backend.services.energy.inverter_preset_loader import build_broadcast_task_records
from backend.schemas.energetic_object import EnergeticObjectCreate, EnergeticObjectUpdate


@dataclass
class _FakeScalars:
    value: Optional[Any]

    def first(self):
        return self.value

    def all(self):
        if self.value is None:
            return []
        return self.value if isinstance(self.value, list) else [self.value]


class _FakeExecuteResult:
    def __init__(self, existing: Optional[Any] = None, scalar: int = 0, rowcount: int = 1):
        self._existing = existing
        self._scalar = scalar
        self.rowcount = rowcount

    def scalars(self):
        return _FakeScalars(self._existing)

    def scalar_one(self):
        return self._scalar


class _FakeAsyncSession:
    def __init__(self, existing: Optional[Any] = None, scalar: int = 0, scalar_sequence: Optional[list[int]] = None, rowcount: int = 1):
        self._existing = existing
        self._scalar = scalar
        self._scalar_sequence = list(scalar_sequence or [])
        self._rowcount = rowcount
        self.added: list[Any] = []
        self.executed: list[str] = []
        self.committed = False

    async def execute(self, query):
        self.executed.append(str(query))
        scalar = self._scalar_sequence.pop(0) if self._scalar_sequence else self._scalar
        return _FakeExecuteResult(self._existing, scalar, self._rowcount)

    def add(self, obj):
        if isinstance(obj, EnergeticObject) and not obj.id:
            obj.id = "test-object-id"
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True

    async def refresh(self, _obj):
        return None


class CreateEnergeticObjectPollingTasksTests(unittest.IsolatedAsyncioTestCase):
    def test_pi30_preset_builds_pi30_broadcast_records(self):
        records = build_broadcast_task_records(
            preset={
                "interval_ms": 5000,
                "commands": [
                    {
                        "suffix": "fw_status",
                        "command_type": "pi30",
                        "pi30_command": "FWSTATUS",
                    }
                ],
            },
            session_id="COR-DEVICE",
            slave_id=1,
            host=None,
            port=None,
            task_name_prefix="obj:COR-DEVICE",
            created_by="owner-cor-id",
            is_active=True,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["command_type"], "pi30")
        self.assertIn("pi30", records[0]["command_payload"])
        self.assertEqual(records[0]["command_payload"]["command_name"], "fw_status")

    def test_pi30_preset_keeps_raw_hex_payload(self):
        records = build_broadcast_task_records(
            preset={
                "interval_ms": 5000,
                "commands": [
                    {
                        "suffix": "Clear",
                        "command_type": "pi30",
                        "pi30_hex": "0D",
                    }
                ],
            },
            session_id="COR-DEVICE",
            slave_id=1,
            host=None,
            port=None,
            task_name_prefix="obj:COR-DEVICE",
            created_by="owner-cor-id",
            is_active=True,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["command_type"], "pi30")
        self.assertEqual(records[0]["command_payload"]["pi30"], "0D")
        self.assertEqual(records[0]["command_payload"]["command_name"], "Clear")

    def test_modbus_tcp_unit_id_fallbacks_to_object_slave_id(self):
        records = build_broadcast_task_records(
            preset={
                "interval_ms": 5000,
                "commands": [
                    {
                        "suffix": "grid",
                        "command_type": "modbus_tcp",
                        "function_code": 3,
                        "start_address": 3,
                        "quantity": 26,
                    }
                ],
            },
            session_id="COR-DEVICE",
            slave_id=100,
            host="91.203.25.12",
            port=502,
            task_name_prefix="obj:COR-DEVICE",
            created_by="owner-cor-id",
            is_active=True,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["command_type"], "modbus_tcp")
        self.assertEqual(records[0]["command_payload"]["unit_id"], 100)

    async def test_victron_object_creates_expected_default_tasks(self):
        db = _FakeAsyncSession()
        payload = EnergeticObjectCreate(
            name="victron-test-object",
            protocol="modbus_tcp",
            modbus_config_file="victron_cerbo_gx.json",
        )

        created = await create_energetic_object(db, payload)

        self.assertEqual(created.id, "test-object-id")
        tasks = [obj for obj in db.added if isinstance(obj, DevicePollingTask)]
        self.assertEqual(len(tasks), 3)

        expected = [
            ("modbus_registers", {"preset": "grid_monitoring"}, 5000),
            ("schedule_check", {}, 3000),
            ("cerbo_collection", {}, 2000),
        ]

        actual = [(task.task_type, task.command_config, int(task.interval_ms)) for task in tasks]
        self.assertEqual(actual, expected)
        self.assertTrue(all(task.is_active for task in tasks))
        self.assertTrue(all(task.energetic_object_id == "test-object-id" for task in tasks))
        self.assertTrue(db.committed)

    async def test_deye_object_creates_expected_default_task(self):
        db = _FakeAsyncSession()
        payload = EnergeticObjectCreate(
            name="deye-test-object",
            protocol="modbus_over_tcp",
            modbus_config_file="deye_inverter.json",
        )

        created = await create_energetic_object(db, payload)

        self.assertEqual(created.id, "test-object-id")
        tasks = [obj for obj in db.added if isinstance(obj, DevicePollingTask)]
        self.assertEqual(len(tasks), 1)

        task = tasks[0]
        self.assertEqual(task.task_type, "modbus_registers")
        self.assertEqual(
            task.command_config,
            {
                "register_groups": [
                    "grid_phase_voltage_power",
                    "grid_current",
                    "gen_relay_status",
                    "generator_extended",
                    "battery",
                    "load",
                    "service_extended",
                    "inverter",
                    "solar",
                    "solar_high",
                    "power32_v104",
                    "energy_service",
                ]
            },
        )
        self.assertEqual(int(task.interval_ms), 15000)
        self.assertTrue(task.is_active)
        self.assertEqual(task.energetic_object_id, "test-object-id")
        self.assertTrue(db.committed)

    async def test_other_config_creates_no_default_tasks(self):
        db = _FakeAsyncSession()
        payload = EnergeticObjectCreate(
            name="other-test-object",
            protocol="modbus_tcp",
            modbus_config_file="some_other_config.json",
        )

        await create_energetic_object(db, payload)

        tasks = [obj for obj in db.added if isinstance(obj, DevicePollingTask)]
        self.assertEqual(tasks, [])

    async def test_inactive_cor_bridge_object_creates_inactive_preset_tasks(self):
        db = _FakeAsyncSession()
        payload = EnergeticObjectCreate(
            name="inactive-cor-object",
            vendor="COR",
            model_name="FW-001-20Kw-Q",
            protocol="cor_bridge",
            cor_bridges=["COR-8C4B14DF4C24"],
            slave_ids=[1],
            is_active=False,
        )

        await create_energetic_object(db, payload, owner_cor_id="owner-cor-id")

        broadcast_tasks = [obj for obj in db.added if isinstance(obj, WebSocketBroadcastTask)]
        self.assertEqual(len(broadcast_tasks), 4)
        self.assertTrue(all(task.session_id == "COR-8C4B14DF4C24" for task in broadcast_tasks))
        self.assertTrue(all(task.command_type == "pi30" for task in broadcast_tasks))
        self.assertTrue(all(task.is_active is False for task in broadcast_tasks))
        self.assertEqual(
            {task.command_payload["command_name"] for task in broadcast_tasks},
            {"fw_dc_status", "fw_motor_status", "fw_generator_status", "fw_status"},
        )

    async def test_axioma_ismppt_cor_bridge_object_creates_pi30_preset_tasks(self):
        db = _FakeAsyncSession()
        payload = EnergeticObjectCreate(
            name="Серверная 2-й этаж",
            vendor="Axioma",
            model_name="ISMPPT-BFP-11000",
            protocol="cor_bridge",
            cor_bridges=["COR-70B8F662B424"],
            is_active=True,
        )

        await create_energetic_object(db, payload, owner_cor_id=None)

        broadcast_tasks = [obj for obj in db.added if isinstance(obj, WebSocketBroadcastTask)]
        self.assertEqual(len(broadcast_tasks), 3)
        tasks_by_command = {
            task.command_payload["command_name"]: task
            for task in broadcast_tasks
        }
        self.assertEqual(set(tasks_by_command), {"QPIGS", "QPIWS", "QMOD"})

        qpigs_task = tasks_by_command["QPIGS"]
        self.assertEqual(qpigs_task.task_name, "Серверная 2-й этаж:COR-70B8F662B424:QPIGS")
        self.assertEqual(qpigs_task.command_payload["pi30"], "51 50 49 47 53 B7 A9 0D")

        qpiws_task = tasks_by_command["QPIWS"]
        self.assertEqual(qpiws_task.task_name, "Серверная 2-й этаж:COR-70B8F662B424:QPIWS")
        self.assertEqual(qpiws_task.command_payload["pi30"], "51 50 49 57 53 B4 DA 0D")

        qmod_task = tasks_by_command["QMOD"]
        self.assertEqual(qmod_task.task_name, "Серверная 2-й этаж:COR-70B8F662B424:QMOD")
        self.assertEqual(qmod_task.command_payload["pi30"], "51 4D 4F 44 49 C1 0D")

        self.assertTrue(all(task.session_id == "COR-70B8F662B424" for task in broadcast_tasks))
        self.assertTrue(all(task.command_type == "pi30" for task in broadcast_tasks))
        self.assertTrue(all(int(task.interval_ms) == 3000 for task in broadcast_tasks))
        self.assertTrue(all(task.is_active for task in broadcast_tasks))

    async def test_daxtromn_cor_bridge_object_creates_qpigs_default_preset_task(self):
        db = _FakeAsyncSession()
        payload = EnergeticObjectCreate(
            name="Мой дом",
            vendor="Daxtromn",
            model_name="AGH-10.2KW-PLUS-Dual-MPPT",
            protocol="cor_bridge",
            cor_bridges=["COR-08D1F99A1AD0"],
            is_active=True,
        )

        await create_energetic_object(db, payload, owner_cor_id="2ACURV11R-1983M")

        broadcast_tasks = [obj for obj in db.added if isinstance(obj, WebSocketBroadcastTask)]
        self.assertEqual(len(broadcast_tasks), 4)
        tasks_by_command = {
            task.command_payload["command_name"]: task
            for task in broadcast_tasks
        }
        self.assertEqual(set(tasks_by_command), {"QPIGS", "QPIWS", "QMOD", "Clear"})

        qpigs_task = tasks_by_command["QPIGS"]
        self.assertEqual(qpigs_task.task_name, "Мой дом:COR-08D1F99A1AD0:QPIGS")
        self.assertEqual(qpigs_task.command_payload["pi30"], "51 50 49 47 53 B7 A9 0D")

        clear_task = tasks_by_command["Clear"]
        self.assertEqual(clear_task.task_name, "Мой дом:COR-08D1F99A1AD0:Clear")
        self.assertEqual(clear_task.command_payload["pi30"], "0D")

        self.assertTrue(all(task.session_id == "COR-08D1F99A1AD0" for task in broadcast_tasks))
        self.assertTrue(all(task.command_type == "pi30" for task in broadcast_tasks))
        self.assertTrue(all(int(task.interval_ms) == 3000 for task in broadcast_tasks))
        self.assertTrue(all(task.created_by == "2ACURV11R-1983M" for task in broadcast_tasks))
        self.assertTrue(all(task.is_active for task in broadcast_tasks))

    async def test_description_only_update_resyncs_direct_polling_tasks(self):
        db = _FakeAsyncSession(scalar=1)
        existing = EnergeticObject(
            id="test-object-id",
            name="Deye object",
            protocol="modbus_over_tcp",
            modbus_config_file="deye_inverter.json",
            description="before",
        )

        with patch(
            "backend.repository.energy.cerbo_service.get_energetic_object",
            new=AsyncMock(return_value=existing),
        ):
            updated = await update_energetic_object(
                db,
                "test-object-id",
                EnergeticObjectUpdate(description="after"),
                owner_cor_id="owner-cor-id",
            )

        self.assertEqual(updated.description, "after")
        tasks = [obj for obj in db.added if isinstance(obj, DevicePollingTask)]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].task_type, "modbus_registers")
        self.assertEqual(int(tasks[0].interval_ms), 15000)
        self.assertTrue(db.committed)

    async def test_delete_energetic_object_cleans_polling_tasks_without_cascade(self):
        db = _FakeAsyncSession(scalar_sequence=[0, 0], rowcount=1)

        deleted = await delete_energetic_object(db, "test-object-id", cascade=False)

        self.assertTrue(deleted)
        self.assertTrue(any("DELETE FROM device_polling_tasks" in sql for sql in db.executed))
        self.assertTrue(any("DELETE FROM energetic_objects" in sql for sql in db.executed))
        self.assertTrue(db.committed)

    async def test_update_backfills_missing_cor_bridge_tasks_for_existing_preset_object(self):
        db = _FakeAsyncSession(
            existing=[
                EnergeticDevice(
                    id="6492e15b-ab2e-4f60-b8f0-d85adf9b40d0",
                    device_id="COR-70B8F66247DC",
                    owner_cor_id="owner-cor-id",
                    name="Bridge",
                )
            ],
            scalar=0,
        )
        existing = EnergeticObject(
            id="test-object-id",
            name="Дом Тиграна",
            model_name="SUN-30K-SG02HP3-EU",
            vendor="Deye",
            protocol="cor_bridge",
            cor_bridges=["6492e15b-ab2e-4f60-b8f0-d85adf9b40d0"],
            slave_ids=[1],
            description="before",
        )

        with patch(
            "backend.repository.energy.cerbo_service.get_energetic_object",
            new=AsyncMock(return_value=existing),
        ):
            updated = await update_energetic_object(
                db,
                "test-object-id",
                EnergeticObjectUpdate(description="after"),
                owner_cor_id="owner-cor-id",
            )

        self.assertEqual(updated.description, "after")
        self.assertEqual(updated.cor_bridges, ["COR-70B8F66247DC"])
        broadcast_tasks = [obj for obj in db.added if isinstance(obj, WebSocketBroadcastTask)]
        self.assertEqual(len(broadcast_tasks), 8)
        self.assertEqual(
            {task.command_payload["command_name"] for task in broadcast_tasks},
            {"grid", "batt", "PV1-4", "PV5-8", "load", "gen", "service", "settings"},
        )
        self.assertTrue(all(task.session_id == "COR-70B8F66247DC" for task in broadcast_tasks))
        self.assertTrue(db.committed)

    async def test_update_backfills_victron_modbus_tcp_bridge_task(self):
        db = _FakeAsyncSession(
            existing=[
                EnergeticDevice(
                    id="6492e15b-ab2e-4f60-b8f0-d85adf9b40d0",
                    device_id="COR-E95",
                    owner_cor_id="owner-cor-id",
                    name="Bridge",
                )
            ],
            scalar=0,
        )
        existing = EnergeticObject(
            id="test-object-id",
            name="COR-AZK",
            model_name="MultiPlus",
            vendor="Victron",
            protocol="modbus_tcp",
            ip_address="91.203.25.12",
            port=502,
            cor_bridges=["6492e15b-ab2e-4f60-b8f0-d85adf9b40d0"],
            slave_ids=[100],
            description="before",
        )

        with patch(
            "backend.repository.energy.cerbo_service.get_energetic_object",
            new=AsyncMock(return_value=existing),
        ):
            updated = await update_energetic_object(
                db,
                "test-object-id",
                EnergeticObjectUpdate(description="after"),
                owner_cor_id="owner-cor-id",
            )

        self.assertEqual(updated.description, "after")
        self.assertEqual(updated.cor_bridges, ["COR-E95"])
        broadcast_tasks = [obj for obj in db.added if isinstance(obj, WebSocketBroadcastTask)]
        self.assertEqual(len(broadcast_tasks), 1)
        task = broadcast_tasks[0]
        self.assertEqual(task.command_type, "modbus_tcp")
        self.assertEqual(task.command_payload["ip"], "91.203.25.12")
        self.assertEqual(task.command_payload["port"], 502)
        self.assertEqual(task.command_payload["unit_id"], 227)
        self.assertEqual(task.command_payload["func"], 3)
        self.assertEqual(task.command_payload["start_addr"], 3)
        self.assertEqual(task.command_payload["quantity"], 26)
        self.assertEqual(task.command_payload["command_name"], "grid")
        self.assertTrue(db.committed)

    async def test_update_rebuilds_cor_bridge_tasks_when_inverter_preset_changes(self):
        db = _FakeAsyncSession(
            existing=[
                EnergeticDevice(
                    id="6492e15b-ab2e-4f60-b8f0-d85adf9b40d0",
                    device_id="COR-70B8F66247DC",
                    owner_cor_id="owner-cor-id",
                    name="Bridge",
                )
            ],
        )
        existing = EnergeticObject(
            id="test-object-id",
            name="Bridge object",
            model_name="SUN-30K-SG02HP3-EU",
            vendor="Deye",
            protocol="cor_bridge",
            cor_bridges=["6492e15b-ab2e-4f60-b8f0-d85adf9b40d0"],
            slave_ids=[1],
            description="before",
        )

        with patch(
            "backend.repository.energy.cerbo_service.get_energetic_object",
            new=AsyncMock(return_value=existing),
        ):
            updated = await update_energetic_object(
                db,
                "test-object-id",
                EnergeticObjectUpdate(
                    vendor="Victron",
                    model_name="MultiPlus",
                    protocol="modbus_tcp",
                    ip_address="91.203.25.12",
                    port=502,
                    slave_ids=[100],
                ),
                owner_cor_id="owner-cor-id",
            )

        self.assertEqual(updated.vendor, "Victron")
        self.assertEqual(updated.model_name, "MultiPlus")
        self.assertEqual(updated.cor_bridges, ["COR-70B8F66247DC"])
        self.assertTrue(
            any("DELETE FROM websocket_broadcast_tasks" in sql for sql in db.executed),
            msg="Expected stale websocket broadcast tasks cleanup query",
        )
        broadcast_tasks = [obj for obj in db.added if isinstance(obj, WebSocketBroadcastTask)]
        self.assertEqual(len(broadcast_tasks), 1)
        task = broadcast_tasks[0]
        self.assertEqual(task.task_name, "Bridge object:COR-70B8F66247DC:grid")
        self.assertEqual(task.session_id, "COR-70B8F66247DC")
        self.assertEqual(task.command_type, "modbus_tcp")
        self.assertEqual(task.command_payload["command_name"], "grid")
        self.assertTrue(db.committed)

    async def test_update_removes_stale_bridge_tasks_when_cor_bridges_cleared(self):
        db = _FakeAsyncSession(scalar=1)
        existing = EnergeticObject(
            id="test-object-id",
            name="Тест",
            model_name="SUN-30K-SG02HP3-EU",
            vendor="Deye",
            protocol="cor_bridge",
            cor_bridges=["COR-345F452DEAF4"],
            slave_ids=[1],
            description="before",
        )

        with patch(
            "backend.repository.energy.cerbo_service.get_energetic_object",
            new=AsyncMock(return_value=existing),
        ):
            updated = await update_energetic_object(
                db,
                "test-object-id",
                EnergeticObjectUpdate(cor_bridges=[]),
                owner_cor_id="owner-cor-id",
            )

        self.assertEqual(updated.cor_bridges, [])
        self.assertTrue(
            any("DELETE FROM websocket_broadcast_tasks" in sql for sql in db.executed),
            msg="Expected stale websocket broadcast tasks cleanup query",
        )
        self.assertTrue(db.committed)


if __name__ == "__main__":
    unittest.main()
