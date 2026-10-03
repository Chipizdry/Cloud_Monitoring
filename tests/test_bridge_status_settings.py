import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import WebSocketDisconnect

from backend.routes.devices import websocket as device_websocket
from backend.routes.devices import websocket_routes as worker_websocket
from backend.services.energy import modbus_cache


STATUS_MODULES = (device_websocket, worker_websocket)


class BridgeStatusSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_request_asks_for_test_settings(self):
        for module in STATUS_MODULES:
            with self.subTest(module=module.__name__):
                redis = SimpleNamespace(set=AsyncMock(return_value=True))
                manager = SimpleNamespace(send_to_session=AsyncMock(return_value=True))
                with patch.object(module, "redis_client", redis), patch.object(
                    module, "websocket_events_manager", manager
                ):
                    await module._request_system_settings_for_online_devices({"bridge-1"})

                manager.send_to_session.assert_awaited_once_with(
                    session_id="bridge-1",
                    event_data={
                        "command_type": "get_settings",
                        "settings_requested": ["test"],
                    },
                )

    def test_test_response_is_extracted_from_device_snapshot(self):
        snapshot = {
            "events": {
                "settings": {
                    "device_id": "COR-8C4B14DF4C34",
                    "timestamp": "2026-09-23T09:54:22.284630",
                    "data": {
                        "command_type": "settings_response",
                        "category": "test",
                        "data": {
                            "build_number": 427,
                            "build_date": "2026-09-07_10-11-08",
                            "node_name": "Для Ивана",
                        },
                    },
                }
            }
        }
        for module in STATUS_MODULES:
            with self.subTest(module=module.__name__):
                settings = module._extract_system_settings_from_snapshot(snapshot)
                self.assertEqual(settings["build_number"], 427)
                self.assertEqual(settings["build_date"], "2026-09-07_10-11-08")
                self.assertEqual(settings["node_name"], "Для Ивана")
                self.assertEqual(settings["settings_timestamp"], "2026-09-23T09:54:22.284630")

    async def test_test_response_is_not_replaced_by_unnamed_event(self):
        values = {}

        async def get(key):
            return values.get(key)

        async def setex(key, _ttl, value):
            values[key] = value

        redis = SimpleNamespace(get=get, setex=setex)
        with patch.object(modbus_cache, "redis_client", redis):
            await modbus_cache.set_cor_agent_snapshot_cache(
                "bridge-1",
                {
                    "timestamp": "2026-09-23T09:54:22.284630",
                    "data": {
                        "command_type": "settings_response",
                        "category": "test",
                        "data": {"build_number": 427, "node_name": "Для Ивана"},
                    },
                },
            )
            await modbus_cache.set_cor_agent_snapshot_cache(
                "bridge-1", {"data": {"other_event": True}}
            )
            snapshot = await modbus_cache.get_cor_agent_snapshot_cache("bridge-1")

        self.assertIn("settings_response:test", snapshot["events"])
        self.assertIn("unknown", snapshot["events"])
        self.assertEqual(
            device_websocket._extract_system_settings_from_snapshot(snapshot)["build_number"],
            427,
        )

    async def test_missing_settings_are_requested_again_on_status_tick(self):
        snapshot = {
            "devices": [
                {"device_id": "bridge-1", "is_online": True, "system_settings": None},
                {"device_id": "bridge-2", "is_online": True, "system_settings": {}},
                {"device_id": "bridge-3", "is_online": False, "system_settings": None},
            ],
            "count": 3,
            "timestamp": "2026-09-28T07:08:38",
        }
        for module in STATUS_MODULES:
            with self.subTest(module=module.__name__):
                websocket = SimpleNamespace(
                    accept=AsyncMock(),
                    send_json=AsyncMock(),
                    receive_text=AsyncMock(
                        side_effect=[asyncio.TimeoutError(), WebSocketDisconnect()]
                    ),
                )
                request_settings = AsyncMock()
                patches = [
                    patch.object(module, "_build_cor_bridge_status_snapshot", AsyncMock(return_value=snapshot)),
                    patch.object(module, "_request_system_settings_for_online_devices", request_settings),
                ]
                if module is device_websocket:
                    patches.append(
                        patch.object(module, "_authenticate_frontend_websocket", AsyncMock(return_value=object()))
                    )
                with patches[0], patches[1]:
                    if len(patches) == 3:
                        with patches[2]:
                            await module.websocket_cor_bridge_statuses(websocket, db=None)
                    else:
                        await module.websocket_cor_bridge_statuses(websocket)

                self.assertEqual(request_settings.await_count, 2)
                self.assertEqual(request_settings.await_args_list[1].args[0], {"bridge-1"})
                self.assertEqual(websocket.send_json.await_args_list[1].args[0]["type"], "cor_bridge_statuses")
