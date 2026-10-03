import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.database.models import CerboMeasurement, PowerMeasurement
from backend.repository.energy.power_measurements import (
    _is_event_fresh_for_history,
    _parse_deye_modbus_read_event,
    build_power_measurement_from_snapshot,
    get_power_history_paginated,
    measurement_to_cerbo_response,
)


@dataclass
class _FakeScalars:
    values: list

    def all(self):
        return self.values


class _FakeExecuteResult:
    def __init__(self, values=None, scalar_value=None):
        if values is None:
            values = []
        self._values = values
        self._scalar_value = scalar_value

    def scalars(self):
        return _FakeScalars(self._values)

    def scalar_one(self):
        return self._scalar_value


class _FakeAsyncSession:
    def __init__(self, execute_results):
        self._execute_results = list(execute_results)

    async def execute(self, _query):
        if not self._execute_results:
            raise AssertionError("Unexpected execute call")
        return self._execute_results.pop(0)


class PowerMeasurementsTests(unittest.IsolatedAsyncioTestCase):
    def test_build_power_measurement_from_deye_snapshot(self):
        measured_at = datetime(2026, 9, 3, 12, 0, 0)
        measurement = build_power_measurement_from_snapshot(
            object_id="object-id",
            object_name="Deye",
            source_protocol="modbus_over_tcp",
            source_task_id="task-id",
            snapshot={
                "measured_at": measured_at,
                "total_power": -120.0,
                "battery1_power": 80.0,
                "battery2_power": 20.0,
                "battery1_soc": 77.0,
                "load_total_power_high": 640.0,
                "pv_total_power_raw_high": 920.0,
                "gen_total_power": 0.0,
            },
        )

        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.grid_power_w, -120.0)
        self.assertEqual(measurement.battery_power_w, 100.0)
        self.assertEqual(measurement.battery_soc, 77.0)
        self.assertEqual(measurement.load_power_w, 640.0)
        self.assertEqual(measurement.solar_power_w, 920.0)
        self.assertEqual(measurement.generator_power_w, 0.0)

    def test_build_power_measurement_prefers_deye_nonzero_soc_and_load_high(self):
        measured_at = datetime(2026, 9, 10, 8, 11, 53)
        measurement = build_power_measurement_from_snapshot(
            object_id="object-id",
            object_name="Щитовая офис",
            source_protocol="modbus_over_tcp",
            source_task_id="task-id",
            snapshot={
                "measured_at": measured_at,
                "total_power": -2300,
                "battery1_soc": 0,
                "battery2_soc": 74,
                "battery1_voltage": 552.0,
                "general_battery_power": -18860,
                "inverter_total_ac_output": 0,
                "load_total_power_high": 12640,
                "pv_total_power_raw_high": 41680,
            },
        )

        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.battery_soc, 74.0)
        self.assertEqual(measurement.battery_voltage_v, 552.0)
        self.assertEqual(measurement.load_power_w, 12640.0)
        self.assertEqual(measurement.grid_power_w, -2300.0)
        self.assertEqual(measurement.solar_power_w, 41680.0)

    def test_build_power_measurement_prefers_first_deye_battery_soc(self):
        measurement = build_power_measurement_from_snapshot(
            object_id="object-id",
            object_name="Щитовая офис",
            snapshot={
                "soc": 0,
                "battery1_soc": 63,
                "battery2_soc": 74,
            },
        )

        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.battery_soc, 63.0)

    def test_build_power_measurement_calculates_deye_voltage_soc_before_second_battery(self):
        measurement = build_power_measurement_from_snapshot(
            object_id="object-id",
            object_name="Щитовая офис",
            snapshot={
                "battery1_soc": 0,
                "battery2_soc": 74,
                "battery1_voltage": 552.0,
                "battery2_voltage": 550.0,
                "battery_work_mode": 0,
                "battery_float_voltage": 600.0,
                "battery_voltage_shutdown": 500.0,
            },
        )

        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.battery_soc, 51.0)

    def test_build_power_measurement_uses_deye_load_low_high_words(self):
        measurement = build_power_measurement_from_snapshot(
            object_id="object-id",
            object_name="Щитовая офис",
            snapshot={
                "load_total_power_low": 12640,
                "load_total_power_high": 0,
            },
        )

        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.load_power_w, 12640.0)

    def test_build_power_measurement_accepts_deye_frontend_field_names(self):
        measurement = build_power_measurement_from_snapshot(
            object_id="object-id",
            object_name="Щитовая офис",
            snapshot={
                "battery1SOC": 0,
                "battery2SOC": 74,
                "batteryTotalPower": 6200,
                "LoadTotalPower": 12640,
                "TotalPVPower": 42560,
                "inputPowerTotal": -6796,
            },
        )

        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.battery_soc, 74.0)
        self.assertEqual(measurement.battery_power_w, 6200.0)
        self.assertEqual(measurement.load_power_w, 12640.0)
        self.assertEqual(measurement.solar_power_w, 42560.0)
        self.assertEqual(measurement.grid_power_w, -6796.0)

    def test_deye_cor_bridge_measurement_prefers_live_ui_field_names(self):
        measurement = build_power_measurement_from_snapshot(
            object_id="object-id",
            object_name="Дом Тиграна",
            source_protocol="cor_bridge_modbus_ws",
            snapshot={
                "batteryTotalPower": -50,
                "general_battery_power": 0,
                "battery1Voltage": 551.2,
                "battery2Voltage": 550.8,
                "battery1SOC": 99.9,
                "PVTotalPower_low": 3170,
                "PVTotalPower_high": 0,
                "solar_total_pv_power": 9130,
                "LoadTotalPower": 2434,
                "inverter_total_ac_output": 2453,
                "inputPowerTotal": -623,
                "ess_total_input_power": -1978,
            },
        )

        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.battery_power_w, -50.0)
        self.assertEqual(measurement.battery_voltage_v, 551.2)
        self.assertEqual(measurement.battery_soc, 99.9)
        self.assertEqual(measurement.solar_power_w, 3170.0)
        self.assertEqual(measurement.load_power_w, 2434.0)
        self.assertEqual(measurement.grid_power_w, -623.0)

    def test_build_power_measurement_from_pi30_snapshot(self):
        measured_at = datetime(2026, 9, 3, 12, 0, 0)
        measurement = build_power_measurement_from_snapshot(
            object_id="object-id",
            object_name="Axioma",
            source_protocol="pi30_ws",
            source_task_id="QPIGS",
            snapshot={
                "measured_at": measured_at.isoformat(),
                "solarPower": 700.0,
                "batteryTotalPower": -260.0,
                "batteryVoltage": 52.4,
                "batterySOC": 95,
                "inputPower": 660.0,
                "outputActivePower": 400.0,
            },
        )

        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.measured_at, measured_at)
        self.assertEqual(measurement.solar_power_w, 700.0)
        self.assertEqual(measurement.battery_power_w, -260.0)
        self.assertEqual(measurement.battery_voltage_v, 52.4)
        self.assertEqual(measurement.battery_soc, 95.0)
        self.assertEqual(measurement.grid_power_w, 660.0)
        self.assertEqual(measurement.load_power_w, 400.0)

    def test_parse_deye_cor_bridge_modbus_events_feed_power_measurement(self):
        events = [
            {
                "data": {
                    "command_name": "batt",
                    "hex_response": "01031600FA1590004A004AFFCE0000000000000000000000000000",
                }
            },
            {
                "data": {
                    "command_name": "PV1-4",
                    "hex_response": "010318006400C8012C0190000000000000000000000000000000000000",
                }
            },
            {
                "data": {
                    "command_name": "PV5-8",
                    "hex_response": "01031A01F4025802BC03200000000000000000000000000000000000000000",
                }
            },
            {
                "data": {
                    "command_name": "grid",
                    "hex_response": "01032E000000000000000000000000000000000000FF3800000000000000000000000000000000000000000000000000000000",
                }
            },
            {
                "data": {
                    "command_name": "load",
                    "hex_response": "01032200000000000000000000000000000000000001F400000000000000000000000000000000",
                }
            },
        ]
        snapshot = {}
        for event in events:
            parsed = _parse_deye_modbus_read_event(event)
            self.assertIsNotNone(parsed)
            snapshot.update(parsed[1])
        snapshot["PVTotalPower"] = snapshot["PVTotalPower_low"] + snapshot["PVTotalPower_high"]

        measurement = build_power_measurement_from_snapshot(
            object_id="object-id",
            object_name="Дом Тиграна",
            source_protocol="cor_bridge_modbus_ws",
            source_task_id="load",
            snapshot=snapshot,
        )

        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.battery_soc, 74.0)
        self.assertEqual(measurement.battery_voltage_v, 552.0)
        self.assertEqual(measurement.battery_power_w, -500.0)
        self.assertEqual(measurement.solar_power_w, 36000.0)
        self.assertEqual(measurement.grid_power_w, -200.0)
        self.assertEqual(measurement.load_power_w, 500.0)

    def test_deye_cor_bridge_history_ignores_stale_cached_events(self):
        measured_at = datetime(2026, 9, 14, 8, 10, 0)
        fresh_event = {"timestamp": "2026-09-14T08:09:55"}
        stale_event = {"timestamp": "2026-09-14T08:08:17"}

        self.assertTrue(_is_event_fresh_for_history(fresh_event, measured_at))
        self.assertFalse(_is_event_fresh_for_history(stale_event, measured_at))

        fresh_pv = _parse_deye_modbus_read_event(
            {
                "timestamp": fresh_event["timestamp"],
                "data": {
                    "command_name": "PV1-4",
                    "hex_response": "010318006400C8012C0190000000000000000000000000000000000000",
                },
            }
        )
        stale_pv = _parse_deye_modbus_read_event(
            {
                "timestamp": stale_event["timestamp"],
                "data": {
                    "command_name": "PV5-8",
                    "hex_response": "01031A01F4025802BC03200000000000000000000000000000000000000000",
                },
            }
        )
        self.assertIsNotNone(fresh_pv)
        self.assertIsNotNone(stale_pv)

        snapshot = {}
        snapshot.update(fresh_pv[1])
        snapshot["PVTotalPower"] = snapshot["PVTotalPower_low"] + (
            snapshot.get("PVTotalPower_high") or 0
        )

        measurement = build_power_measurement_from_snapshot(
            object_id="object-id",
            object_name="Дом Тиграна",
            source_protocol="cor_bridge_modbus_ws",
            source_task_id="PV1-4",
            snapshot=snapshot,
        )

        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.solar_power_w, 10000.0)

    def test_power_measurement_adapts_to_legacy_cerbo_response(self):
        measured_at = datetime(2026, 9, 3, 12, 0, 0)
        measurement = PowerMeasurement(
            id="power-id",
            energetic_object_id="object-id",
            object_name="Deye",
            created_at=measured_at,
            measured_at=measured_at,
            solar_power_w=920.0,
            battery_power_w=100.0,
            battery_voltage_v=52.4,
            battery_soc=77.0,
            grid_power_w=-120.0,
            load_power_w=640.0,
        )

        response = measurement_to_cerbo_response(measurement)

        self.assertEqual(response.id, "power-id")
        self.assertEqual(response.solar_total_pv_power, 920.0)
        self.assertEqual(response.general_battery_power, 100.0)
        self.assertEqual(response.battery_voltage, 52.4)
        self.assertEqual(response.soc, 77.0)
        self.assertEqual(response.ess_total_input_power, -120.0)
        self.assertEqual(response.inverter_total_ac_output, 640.0)

    def test_legacy_cerbo_response_includes_battery_voltage(self):
        measured_at = datetime(2026, 9, 16, 12, 0, 0)
        measurement = CerboMeasurement(
            id="cerbo-id",
            energetic_object_id="object-id",
            object_name="COR-AZK",
            created_at=measured_at,
            measured_at=measured_at,
            general_battery_power=120.0,
            battery_voltage=52.4,
            inverter_total_ac_output=640.0,
            ess_total_input_power=-80.0,
            solar_total_pv_power=920.0,
            soc=77.0,
        )

        response = measurement_to_cerbo_response(measurement)

        self.assertEqual(response.id, "cerbo-id")
        self.assertEqual(response.battery_voltage, 52.4)

    def test_power_measurement_response_recovers_deye_zero_fields_from_raw_snapshot(self):
        measured_at = datetime(2026, 9, 10, 8, 11, 53)
        measurement = PowerMeasurement(
            id="power-id",
            energetic_object_id="object-id",
            object_name="Щитовая офис",
            created_at=measured_at,
            measured_at=measured_at,
            solar_power_w=41680.0,
            battery_power_w=-18860.0,
            battery_soc=0.0,
            grid_power_w=-2300.0,
            load_power_w=0.0,
            raw_snapshot={
                "battery1_soc": 0,
                "battery2_soc": 74,
                "battery1_voltage": 552.0,
                "battery2_voltage": 550.0,
                "battery_work_mode": 0,
                "battery_float_voltage": 600.0,
                "battery_voltage_shutdown": 500.0,
                "load_total_power_low": 12640,
                "load_total_power_high": 0,
            },
        )

        response = measurement_to_cerbo_response(measurement)

        self.assertEqual(response.soc, 51.0)
        self.assertEqual(response.inverter_total_ac_output, 12640.0)

    async def test_history_pagination_merges_cerbo_and_power_rows(self):
        now = datetime(2026, 9, 3, 12, 0, 0)
        cerbo = CerboMeasurement(
            id="cerbo-id",
            energetic_object_id="object-id",
            object_name="Victron",
            created_at=now - timedelta(minutes=1),
            measured_at=now - timedelta(minutes=1),
            general_battery_power=10.0,
            inverter_total_ac_output=20.0,
            ess_total_input_power=30.0,
            solar_total_pv_power=40.0,
            soc=50.0,
        )
        power = PowerMeasurement(
            id="power-id",
            energetic_object_id="object-id",
            object_name="Deye",
            created_at=now,
            measured_at=now,
            battery_power_w=100.0,
            grid_power_w=200.0,
        )
        db = _FakeAsyncSession(
            [
                _FakeExecuteResult(scalar_value=1),
                _FakeExecuteResult(scalar_value=1),
                _FakeExecuteResult([cerbo]),
                _FakeExecuteResult([power]),
            ]
        )

        response = await get_power_history_paginated(
            db,
            energetic_object_id="object-id",
            page=1,
            page_size=10,
        )

        self.assertEqual(response.total_count, 2)
        self.assertEqual([item.id for item in response.items], ["power-id", "cerbo-id"])


if __name__ == "__main__":
    unittest.main()
