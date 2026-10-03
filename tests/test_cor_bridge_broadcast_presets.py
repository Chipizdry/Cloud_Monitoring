import unittest

from backend.services.energy.cor_bridge_broadcast_presets import (
    build_cor_bridge_preset_tasks,
    build_modbus_read_command_hex,
    list_cor_bridge_broadcast_presets,
)


class CorBridgeBroadcastPresetTests(unittest.TestCase):
    def test_build_modbus_read_command_hex_calculates_crc(self):
        self.assertEqual(
            build_modbus_read_command_hex(slave_id=1, start_address=0x0256, count=0x0017),
            "01 03 02 56 00 17 E4 6C",
        )
        self.assertEqual(
            build_modbus_read_command_hex(slave_id=1, start_address=0x024A, count=0x000B),
            "01 03 02 4A 00 0B 24 63",
        )

    def test_build_cor_bridge_preset_tasks_creates_expected_modbus_tasks(self):
        tasks = build_cor_bridge_preset_tasks(
            preset_name="cor_70b8f66247dc_default",
            task_name_prefix="bridge-poll",
            session_id="COR-70B8F66247DC",
            slave_id=1,
            interval_ms=5000,
            is_active=True,
            created_by="admin",
        )

        self.assertEqual(len(tasks), 5)
        self.assertEqual(tasks[0].task_name, "bridge-poll:grid")
        self.assertEqual(tasks[0].command_type, "modbus_read")
        self.assertEqual(tasks[0].hex_data, "01 03 02 56 00 17 E4 6C")
        self.assertEqual(tasks[1].hex_data, "01 03 02 4A 00 0B 24 63")
        self.assertEqual(tasks[2].hex_data, "01 03 02 A0 00 0C 44 55")
        self.assertEqual(tasks[3].hex_data, "01 03 02 CE 00 0D E4 48")
        self.assertEqual(tasks[4].hex_data, "01 03 02 84 00 11 C4 57")

    def test_list_presets_contains_alias_for_cor_bridge(self):
        presets = list_cor_bridge_broadcast_presets()
        preset_names = {preset["preset_name"] for preset in presets}
        self.assertIn("cor_bridge_status_polling", preset_names)
        self.assertIn("cor_70b8f66247dc_default", preset_names)


if __name__ == "__main__":
    unittest.main()