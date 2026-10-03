import unittest

from backend.services.energy.polling_manager import _enrich_deye_polling_snapshot


class DeyePollingSnapshotTests(unittest.TestCase):
    def test_enrich_deye_polling_snapshot_prefers_first_battery_soc(self):
        snapshot = {
            "battery1_soc": 63,
            "battery2_soc": 74,
            "load_total_power_low": 12640,
            "load_total_power_high": 0,
        }

        _enrich_deye_polling_snapshot(snapshot)

        self.assertEqual(snapshot["soc"], 63.0)

    def test_enrich_deye_polling_snapshot_matches_frontend_aggregates(self):
        snapshot = {
            "battery1_voltage": 552.0,
            "battery2_voltage": 550.0,
            "battery1_soc": 0,
            "battery2_soc": 0,
            "battery1_power": 3000,
            "battery2_power": 3200,
            "load_total_power_low": 12640,
            "load_total_power_high": 0,
            "pv_total_power_raw_high": 42560,
            "total_power": -6796,
            "battery_work_mode": 0,
            "battery_float_voltage": 600.0,
            "battery_voltage_shutdown": 500.0,
        }

        _enrich_deye_polling_snapshot(snapshot)

        self.assertEqual(snapshot["general_battery_power"], 6200.0)
        self.assertEqual(snapshot["batteryTotalPower"], 6200.0)
        self.assertEqual(snapshot["LoadTotalPower"], 12640.0)
        self.assertEqual(snapshot["inverter_total_ac_output"], 12640.0)
        self.assertEqual(snapshot["solar_total_pv_power"], 42560.0)
        self.assertEqual(snapshot["ess_total_input_power"], -6796.0)
        self.assertEqual(snapshot["soc"], 51.0)
        self.assertEqual(snapshot["calculated_soc"], 51.0)


if __name__ == "__main__":
    unittest.main()
