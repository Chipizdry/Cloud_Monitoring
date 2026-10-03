import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from backend.routes.energy.energetic_objects import (
    OnDemandCommandRequest,
    list_on_demand_commands,
    router as energetic_objects_router,
    send_on_demand_command,
)
from backend.services.energy.inverter_preset_loader import (
    OnDemandValueError,
    build_broadcast_task_records,
    build_on_demand_payload,
    describe_preset,
    get_on_demand_command,
    get_preset,
    on_demand_command_uses_value,
)
from backend.services.energy.pi30_commands import format_pi30_command_with_crc_hex


class OnDemandCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.obj = SimpleNamespace(
            vendor="Axioma",
            model_name="ISMPPT-BFP-11000",
            cor_bridges=["COR-BRIDGE"],
        )

    def test_qpiri_is_listed_and_not_scheduled(self):
        preset = get_preset(self.obj.vendor, self.obj.model_name)
        described = describe_preset(self.obj.vendor, self.obj.model_name)
        self.assertEqual([cmd["name"] for cmd in described["on_demand_commands"]], ["QPIRI"])
        records = build_broadcast_task_records(
            preset=preset, session_id="COR-BRIDGE", slave_id=1, host=None, port=None,
            task_name_prefix="object:COR-BRIDGE", created_by=None,
        )
        self.assertEqual({record["command_payload"]["command_name"] for record in records},
                         {"QPIGS", "QPIWS", "QMOD"})

    def test_qpiri_wire_payload_has_crc_and_cr(self):
        command = get_on_demand_command(self.obj.vendor, self.obj.model_name, "QPIRI")
        self.assertEqual(
            build_on_demand_payload(command),
            {"command_type": "pi30", "pi30": "51 50 49 52 49 F8 54 0D", "command_name": "QPIRI"},
        )
        self.assertEqual(build_on_demand_payload(command, value="IGNORED"), build_on_demand_payload(command))
        self.assertFalse(on_demand_command_uses_value(command))

    def test_value_template_formats_command_before_crc(self):
        command = {"name": "PGR", "command_type": "pi30", "pi30_command": "PGR{value}"}
        self.assertTrue(on_demand_command_uses_value(command))
        self.assertEqual(
            build_on_demand_payload(command, value="00")["pi30"],
            format_pi30_command_with_crc_hex("PGR00"),
        )
        self.assertEqual(
            build_on_demand_payload(command, value=12)["pi30"],
            format_pi30_command_with_crc_hex("PGR12"),
        )
        with self.assertRaises(OnDemandValueError):
            build_on_demand_payload(command)
        with self.assertRaises(OnDemandValueError):
            build_on_demand_payload(command, value="12\rQPIRI")
        with self.assertRaises(OnDemandValueError):
            build_on_demand_payload(command, value=float("nan"))

    def test_command_name_is_required_in_post_body(self):
        with self.assertRaises(ValidationError):
            OnDemandCommandRequest()
        with self.assertRaises(ValidationError):
            OnDemandCommandRequest(command_name="QPIRI", bridge_session_id="OTHER")
        self.assertNotIn("bridge_session_id", OnDemandCommandRequest.model_json_schema()["properties"])
        self.assertIn("value", OnDemandCommandRequest.model_json_schema()["properties"])
        with self.assertRaises(ValidationError):
            OnDemandCommandRequest(command_name="PGR", value=True)
        app = FastAPI()
        app.include_router(energetic_objects_router)
        post_paths = {route.path for route in app.routes if "POST" in getattr(route, "methods", set())}
        self.assertIn("/energetic_objects/{object_id}/commands", post_paths)
        self.assertNotIn("/energetic_objects/{object_id}/commands/{command_name}", post_paths)

    async def test_dispatch_requires_linked_bridge(self):
        with patch("backend.routes.energy.energetic_objects.ensure_object_permission", new=AsyncMock(return_value=self.obj)), \
             patch("backend.routes.energy.energetic_objects.enqueue_on_demand_command", new=AsyncMock()) as enqueue:
            self.obj.cor_bridges = []
            with self.assertRaises(HTTPException) as error:
                await send_on_demand_command(
                    "object-id", OnDemandCommandRequest(command_name="QPIRI"),
                    db=None, current_user=SimpleNamespace(), _=None,
                )
            self.assertEqual(error.exception.status_code, 400)
            enqueue.assert_not_awaited()

    async def test_dispatch_queues_only_named_preset_command(self):
        with patch("backend.routes.energy.energetic_objects.ensure_object_permission", new=AsyncMock(return_value=self.obj)), \
             patch("backend.routes.energy.energetic_objects.enqueue_on_demand_command", new=AsyncMock(return_value="request-id")) as enqueue:
            result = await send_on_demand_command(
                "object-id", OnDemandCommandRequest(command_name="QPIRI", value=123),
                db=None, current_user=SimpleNamespace(), _=None,
            )
            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["bridge_requests"], [{
                "bridge_session_id": "COR-BRIDGE", "status": "queued", "request_id": "request-id",
            }])
            enqueue.assert_awaited_once_with("COR-BRIDGE", build_on_demand_payload(
                get_on_demand_command(self.obj.vendor, self.obj.model_name, "QPIRI")
            ))
            with self.assertRaises(HTTPException) as error:
                await send_on_demand_command(
                    "object-id", OnDemandCommandRequest(command_name="ARBITRARY"),
                    db=None, current_user=SimpleNamespace(), _=None,
                )
            self.assertEqual(error.exception.status_code, 404)

    async def test_dispatch_queues_on_every_linked_bridge(self):
        self.obj.cor_bridges = ["COR-BRIDGE", "COR-OTHER"]
        with patch("backend.routes.energy.energetic_objects.ensure_object_permission", new=AsyncMock(return_value=self.obj)), \
             patch("backend.routes.energy.energetic_objects.enqueue_on_demand_command", new=AsyncMock(side_effect=["id-1", None])) as enqueue:
            result = await send_on_demand_command(
                "object-id", OnDemandCommandRequest(command_name="QPIRI"),
                db=None, current_user=SimpleNamespace(), _=None,
            )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["queued_count"], 1)
        self.assertEqual([item["bridge_session_id"] for item in result["bridge_requests"]], ["COR-BRIDGE", "COR-OTHER"])
        self.assertEqual([item["status"] for item in result["bridge_requests"]], ["queued", "offline"])
        self.assertEqual(enqueue.await_count, 2)

    async def test_dispatch_uses_value_only_for_template_command(self):
        command = {"name": "PGR", "command_type": "pi30", "pi30_command": "PGR{value}"}
        with patch("backend.routes.energy.energetic_objects.ensure_object_permission", new=AsyncMock(return_value=self.obj)), \
             patch("backend.routes.energy.energetic_objects.get_on_demand_command", return_value=command), \
             patch("backend.routes.energy.energetic_objects.enqueue_on_demand_command", new=AsyncMock(return_value="id")) as enqueue:
            with self.assertRaises(HTTPException) as error:
                await send_on_demand_command(
                    "object-id", OnDemandCommandRequest(command_name="PGR"),
                    db=None, current_user=SimpleNamespace(), _=None,
                )
            self.assertEqual(error.exception.status_code, 422)
            enqueue.assert_not_awaited()
            await send_on_demand_command(
                "object-id", OnDemandCommandRequest(command_name="PGR", value="00"),
                db=None, current_user=SimpleNamespace(), _=None,
            )
            enqueue.assert_awaited_once_with("COR-BRIDGE", build_on_demand_payload(command, "00"))

    async def test_dispatch_reports_all_bridges_offline(self):
        self.obj.cor_bridges = ["COR-BRIDGE", "COR-OTHER"]
        with patch("backend.routes.energy.energetic_objects.ensure_object_permission", new=AsyncMock(return_value=self.obj)), \
             patch("backend.routes.energy.energetic_objects.enqueue_on_demand_command", new=AsyncMock(return_value=None)) as enqueue:
            with self.assertRaises(HTTPException) as error:
                await send_on_demand_command(
                    "object-id", OnDemandCommandRequest(command_name="QPIRI"),
                    db=None, current_user=SimpleNamespace(), _=None,
                )
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(enqueue.await_count, 2)

    async def test_list_exposes_only_on_demand_commands(self):
        with patch("backend.routes.energy.energetic_objects.ensure_object_permission", new=AsyncMock(return_value=self.obj)):
            result = await list_on_demand_commands(
                "object-id", db=None, current_user=SimpleNamespace(), _=None,
            )
        self.assertEqual([cmd["name"] for cmd in result["commands"]], ["QPIRI"])
        self.assertEqual(result["commands"][0]["requires_value"], False)
