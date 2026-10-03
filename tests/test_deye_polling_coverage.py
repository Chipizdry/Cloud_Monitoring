import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEYE_FRONTEND_RANGES = {
    "generator": (661, 11),
    "solar_high": (718, 13),
    "battery": (586, 11),
    "solar": (672, 12),
    "load": (644, 17),
    "service": (551, 8),
    "inverter": (621, 18),
    "grid": (598, 23),
    "power_32": (687, 23),
    "energy_service": (101, 30),
    "faults": (553, 6),
}


class DeyePollingCoverageTests(unittest.TestCase):
    def test_direct_polling_covers_every_frontend_read_range(self):
        config = json.loads(
            (PROJECT_ROOT / "backend/modbus_configs/deye_inverter.json").read_text()
        )
        preset = json.loads(
            (PROJECT_ROOT / "backend/ModbusPollingPresets/Deye/default.json").read_text()
        )
        groups = preset["tasks"][0]["command_config"]["register_groups"]
        cached_ranges = [
            (
                config["register_groups"][group]["start_address"],
                config["register_groups"][group]["count"],
            )
            for group in groups
        ]

        for name, (start, count) in DEYE_FRONTEND_RANGES.items():
            end = start + count
            cursor = start
            while cursor < end:
                covering = [
                    (range_start, range_start + range_count)
                    for range_start, range_count in cached_ranges
                    if range_start <= cursor < range_start + range_count
                ]
                self.assertTrue(covering, f"{name}: register {cursor} is not polled")
                cursor = min(end, max(range_end for _, range_end in covering))
