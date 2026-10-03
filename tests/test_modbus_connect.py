import asyncio
import threading
import time
import unittest
from unittest.mock import patch

from backend.services.energy import modbus_client


class DeyeConnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_connect_does_not_block_api_event_loop(self):
        started = threading.Event()
        release = threading.Event()
        attempts = 0

        def unavailable_connect(client):
            nonlocal attempts
            attempts += 1
            started.set()
            release.wait(timeout=0.5)
            client.last_connect_error = "No route to host"
            return False

        with patch.object(modbus_client.ModbusTCP, "connect", unavailable_connect):
            started_at = time.monotonic()
            tasks = [
                asyncio.create_task(
                    modbus_client.get_or_create_modbus_client(
                        protocol="modbus_over_tcp",
                        ip_address="192.0.2.1",
                        port=502,
                        object_id="unreachable-test-device",
                        connect_retries=0,
                    )
                )
                for _ in range(12)
            ]
            try:
                await asyncio.sleep(0.02)
                self.assertTrue(started.is_set())
                self.assertLess(time.monotonic() - started_at, 0.2)
            finally:
                release.set()

            self.assertEqual(await asyncio.gather(*tasks), [None] * 12)
            self.assertEqual(attempts, 1)


if __name__ == "__main__":
    unittest.main()
