import json
import struct
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Union
from unittest.mock import AsyncMock, patch

from pydantic import TypeAdapter

from backend.repository.energy import energy_meter_measurements as meter
from backend.repository.energy.power_measurements import _parse_deye_modbus_read_event
from backend.routes.energy import cerbo_routes
from backend.schemas.device_measurement import (
    CerboMeasurementResponse,
    EnergyMeterMeasurementResponse,
    PaginatedResponse,
)
from backend.services.energy.cor_bridge_broadcast_presets import calculate_modbus_rtu_crc
from backend.services.energy import modbus_cache


MEASURED_AT = datetime(2026, 9, 28, 12, 0, 0)


def _event(suffix, values, *, slave=1, measured_at=MEASURED_AT):
    start, count = meter.TAC_BLOCKS[suffix]
    payload = bytearray(count * 2)
    for address, (kind, value) in values.items():
        struct.pack_into(">" + kind, payload, (address - start) * 2, value)
    frame = bytes((slave, 4, len(payload))) + payload
    return {
        "timestamp": measured_at.isoformat(),
        "data": {
            "cmd": "modbus_read",
            "command_name": suffix,
            "hex_response": (frame + calculate_modbus_rtu_crc(frame)).hex().upper(),
        },
    }


def _cycle(*, slave=1):
    return [
        _event("phases", {
            0x0000: ("f", 230.5), 0x0002: ("f", 231.25), 0x0004: ("f", 229.75),
        }, slave=slave),
        _event("angles", {
            0x002A: ("f", 400.0), 0x002C: ("f", 401.0), 0x002E: ("f", 399.0),
            0x0030: ("f", 50.0), 0x0032: ("f", 12345.0), 0x0034: ("f", -1200.0),
            0x0036: ("f", 12403.0), 0x0038: ("f", 0.995), 0x003A: ("f", -5.55),
        }, slave=slave),
        _event("energy32", {0x0410: ("I", 123456)}, slave=slave),
        _event("energy_float", {0x0510: ("f", 1234.75)}, slave=slave),
    ]


class _Db:
    def __init__(self, objects):
        self.objects = objects
        self.added = []
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, _query):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.objects))

    def add(self, item):
        self.added.append(item)


class _HistoryDb:
    def __init__(self, rows, total_count):
        self.rows = rows
        self.total_count = total_count
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        if len(self.queries) % 2:
            return SimpleNamespace(scalar_one=lambda: self.total_count)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.rows))


class EnergyMeterMeasurementsTests(unittest.IsolatedAsyncioTestCase):
    def test_register_blocks_match_tac_preset(self):
        path = Path(__file__).resolve().parents[1] / "backend/InverterBridgePresets/Taiye/TAC4300CT.json"
        preset = json.loads(path.read_text())
        for command in preset["commands"]:
            if command["suffix"] in meter.TAC_BLOCKS:
                self.assertEqual(
                    meter.TAC_BLOCKS[command["suffix"]],
                    (command["start_address"], command["count"]),
                )

    def test_decodes_scaled_values_and_prefers_float_energy(self):
        reading = meter.build_tac4300ct_reading(_cycle(), measured_at=MEASURED_AT, slave_id=1)
        self.assertAlmostEqual(reading["active_power_kw"], 12.345)
        self.assertAlmostEqual(reading["reactive_power_kvar"], -1.2)
        self.assertAlmostEqual(reading["apparent_power_kva"], 12.403)
        self.assertAlmostEqual(reading["power_factor"], 0.995, places=5)
        self.assertAlmostEqual(reading["frequency_hz"], 50)
        self.assertAlmostEqual(reading["angle_deg"], -5.55, places=4)
        self.assertAlmostEqual(reading["voltage_l1_n_v"], 230.5)
        self.assertAlmostEqual(reading["voltage_l2_n_v"], 231.25)
        self.assertAlmostEqual(reading["voltage_l3_n_v"], 229.75)
        self.assertAlmostEqual(reading["voltage_l1_l2_v"], 400)
        self.assertAlmostEqual(reading["voltage_l2_l3_v"], 401)
        self.assertAlmostEqual(reading["voltage_l3_l1_v"], 399)
        self.assertAlmostEqual(reading["apparent_energy_kvah"], 1234.75)

    def test_uses_scaled_int_energy_when_float_is_missing(self):
        reading = meter.build_tac4300ct_reading(_cycle()[:3], measured_at=MEASURED_AT, slave_id=1)
        self.assertAlmostEqual(reading["apparent_energy_kvah"], 1234.56)

    def test_rejects_bad_crc_stale_blocks_and_other_slave(self):
        cycle = _cycle()
        broken = dict(cycle[1], data=dict(cycle[1]["data"]))
        broken["data"]["hex_response"] = broken["data"]["hex_response"][:-2] + "00"
        self.assertIsNone(meter.build_tac4300ct_reading(
            [cycle[0], broken, cycle[3]], measured_at=MEASURED_AT, slave_id=1,
        ))
        stale = dict(cycle[0], timestamp=(MEASURED_AT - timedelta(seconds=16)).isoformat())
        self.assertIsNone(meter.build_tac4300ct_reading(
            [stale, cycle[1], cycle[3]], measured_at=MEASURED_AT, slave_id=1,
        ))
        self.assertIsNone(meter.build_tac4300ct_reading(
            [*_cycle(slave=2)[:1], cycle[1], cycle[3]], measured_at=MEASURED_AT, slave_id=1,
        ))

    def test_short_tac_load_does_not_create_deye_measurement(self):
        load_frame = bytes((1, 4, 12)) + bytes(12)
        load_hex = (load_frame + calculate_modbus_rtu_crc(load_frame)).hex()
        self.assertIsNone(_parse_deye_modbus_read_event({
            "data": {"command_name": "load", "hex_response": load_hex},
        }))

    async def test_angles_response_persists_one_complete_row(self):
        cycle = _cycle()
        db = _Db([SimpleNamespace(id="object-1", slave_ids=[1])])
        snapshot = {"events": {event["data"]["command_name"]: event for event in cycle}}
        with patch.object(
            modbus_cache, "get_cor_agent_snapshot_cache", AsyncMock(return_value=snapshot)
        ), patch("backend.database.db.async_session_maker", return_value=db):
            written = await meter.persist_tac4300ct_measurement_from_ws(
                device_aliases={"COR-123"}, raw_event=cycle[1]["data"], measured_at=MEASURED_AT,
            )
        self.assertTrue(written)
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0].energetic_object_id, "object-1")
        self.assertAlmostEqual(db.added[0].apparent_energy_kvah, 1234.75)
        db.commit.assert_awaited_once()

    async def test_non_angles_response_does_not_write(self):
        self.assertFalse(await meter.persist_tac4300ct_measurement_from_ws(
            device_aliases={"COR-123"}, raw_event=_cycle()[0]["data"], measured_at=MEASURED_AT,
        ))

    async def test_shared_bridge_does_not_assign_reading_to_ambiguous_object(self):
        cycle = _cycle()
        snapshot = {"events": {event["data"]["command_name"]: event for event in cycle}}
        db = _Db([
            SimpleNamespace(id="object-1", slave_ids=[1]),
            SimpleNamespace(id="object-2", slave_ids=[1]),
        ])
        with patch.object(
            modbus_cache, "get_cor_agent_snapshot_cache", AsyncMock(return_value=snapshot)
        ), patch("backend.database.db.async_session_maker", return_value=db):
            written = await meter.persist_tac4300ct_measurement_from_ws(
                device_aliases={"COR-123"}, raw_event=cycle[1]["data"], measured_at=MEASURED_AT,
            )
        self.assertFalse(written)
        self.assertEqual(db.added, [])

    async def test_meter_history_uses_existing_pagination_shape(self):
        row = meter.EnergyMeterMeasurement(
            id="reading-1", energetic_object_id="object-1", created_at=MEASURED_AT,
            measured_at=MEASURED_AT, **meter.build_tac4300ct_reading(
                _cycle(), measured_at=MEASURED_AT, slave_id=1,
            ),
        )
        db = _HistoryDb([row], total_count=11)
        response = await meter.get_energy_meter_history_paginated(
            db, energetic_object_id="object-1", page=2, page_size=10,
            start_date=MEASURED_AT - timedelta(hours=1), end_date=MEASURED_AT,
        )
        self.assertEqual(response.total_count, 11)
        self.assertEqual(response.total_pages, 2)
        self.assertEqual(response.page, 2)
        self.assertEqual(response.items[0].id, "reading-1")
        self.assertAlmostEqual(response.items[0].active_power_kw, 12.345)
        sql = str(db.queries[1].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("energy_meter_measurements.energetic_object_id = 'object-1'", sql)
        self.assertIn("energy_meter_measurements.measured_at >=", sql)
        self.assertIn("LIMIT 10 OFFSET 10", sql)

        api_response = TypeAdapter(
            PaginatedResponse[Union[EnergyMeterMeasurementResponse, CerboMeasurementResponse]]
        ).validate_python(response)
        self.assertAlmostEqual(api_response.items[0].apparent_energy_kvah, 1234.75)

    async def test_existing_measurements_route_selects_table_by_object_model(self):
        old_history = AsyncMock(return_value="power history")
        meter_history = AsyncMock(return_value="meter history")
        with patch.object(cerbo_routes, "get_power_history_paginated", old_history), patch.object(
            cerbo_routes, "get_energy_meter_history_paginated", meter_history
        ):
            meter_result = await cerbo_routes._get_object_measurements(
                db=object(), energetic_object=SimpleNamespace(
                    id="meter-1", vendor="Taiye", model_name="TAC4300CT"
                ), page=1, page_size=10, start_date=None, end_date=None,
            )
            power_result = await cerbo_routes._get_object_measurements(
                db=object(), energetic_object=SimpleNamespace(
                    id="power-1", vendor="Deye", model_name="SUN"
                ), page=1, page_size=10, start_date=None, end_date=None,
            )
        self.assertEqual(meter_result, "meter history")
        self.assertEqual(power_result, "power history")
        meter_history.assert_awaited_once()
        old_history.assert_awaited_once()
