import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from backend.routes.devices.energetic_device import router, run_traceroute_on_device
from backend.schemas.energetic_device import TracerouteRequest


class TracerouteRequestTests(unittest.TestCase):
    def test_accepts_ip_addresses_and_domain_names(self):
        for host in ("8.8.8.8", "2001:db8::1", "example.com", "sub-domain.example.com"):
            with self.subTest(host=host):
                self.assertEqual(TracerouteRequest(session_token="device-1", url=host).url, host)

    def test_rejects_urls_and_command_arguments(self):
        for host in ("", "https://example.com", "example.com/path", "example.com:80", "example.com;id", "-n", "999.999.999.999", "fe80::1%eth0"):
            with self.subTest(host=host), self.assertRaises(ValidationError):
                TracerouteRequest(session_token="device-1", url=host)


class TracerouteRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_route_sends_exact_device_command(self):
        self.assertTrue(any(route.path == "/energetic/traceroute" and "POST" in route.methods for route in router.routes))
        request = TracerouteRequest(session_token="device-1", url="example.com")
        with patch(
            "backend.routes.devices.energetic_device.websocket_events_manager.send_to_session",
            new_callable=AsyncMock,
            return_value=True,
        ) as send:
            response = await run_traceroute_on_device(request)

        send.assert_awaited_once_with(
            session_id="device-1",
            event_data={"command_type": "traceroute", "url": "example.com"},
        )
        self.assertEqual(response["url"], "example.com")

    async def test_disconnected_device_returns_404(self):
        request = TracerouteRequest(session_token="device-1", url="8.8.8.8")
        with patch(
            "backend.routes.devices.energetic_device.websocket_events_manager.send_to_session",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with self.assertRaises(HTTPException) as caught:
                await run_traceroute_on_device(request)

        self.assertEqual(caught.exception.status_code, 404)
