import json
import time
import unittest
from unittest.mock import patch

from backend.services.energy.modbus_cache import (
    get_modbus_register_cache,
    make_register_cache_key,
)


class _FakeRedis:
    def __init__(self, values, indexes):
        self.values = values
        self.indexes = indexes

    async def get(self, key):
        return self.values.get(key)

    async def hgetall(self, key):
        return self.indexes.get(key, {})


class ModbusRegisterCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_assembles_adjacent_polling_ranges(self):
        common = {
            "protocol": "modbus_over_tcp",
            "host": "deye.example",
            "port": 502,
            "slave_id": 1,
            "func_code": 3,
            "object_id": "object-id",
        }
        first_key = make_register_cache_key(start=598, count=12, **common)
        second_key = make_register_cache_key(start=610, count=11, **common)
        now = int(time.time())
        redis = _FakeRedis(
            values={
                first_key: json.dumps({"ok": True, "data": list(range(12)), "cached_at": now - 2}),
                second_key: json.dumps({"ok": True, "data": list(range(12, 23)), "cached_at": now}),
            },
            indexes={
                "modbus:cache:registers:index:modbus_over_tcp:deye.example:502:object-id:1:3": {
                    "598:12": first_key,
                    "610:11": second_key,
                }
            },
        )

        with patch("backend.services.energy.modbus_cache.redis_client", redis):
            cached = await get_modbus_register_cache(
                start=598,
                count=23,
                max_age_seconds=20,
                **common,
            )

        self.assertEqual(cached["data"], list(range(23)))
        self.assertEqual(cached["range_source"], "composite:598:12,610:11")
        self.assertEqual(cached["cached_at"], now - 2)
