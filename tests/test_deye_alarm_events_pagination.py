import unittest
from datetime import datetime
from types import SimpleNamespace

from backend.routes.energy.modbus_tcp import get_deye_alarm_events


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Result:
    def __init__(self, *, scalar_value=None, values=None):
        self._scalar_value = scalar_value
        self._values = values or []

    def scalar_one(self):
        return self._scalar_value

    def scalars(self):
        return _Scalars(self._values)


class _Session:
    def __init__(self, results):
        self.results = list(results)
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        return self.results.pop(0)


class DeyeAlarmEventsPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_page_metadata_and_applies_offset(self):
        event = SimpleNamespace(
            id="event-id",
            energetic_object_id="object-id",
            object_name="Deye",
            measured_at=datetime(2026, 9, 18, 10, 0),
            source_task_id="task-id",
            fault_word_1=1,
            fault_word_2=0,
            warning_word_1=0,
            warning_word_2=0,
            faults=["fault"],
            warnings=[],
            created_at=datetime(2026, 9, 18, 10, 1),
        )
        db = _Session([_Result(scalar_value=25), _Result(values=[event])])

        response = await get_deye_alarm_events(
            object_id="object-id",
            page=3,
            page_size=10,
            limit=None,
            db=db,
        )

        self.assertEqual(response["count"], 1)
        self.assertEqual(response["total_count"], 25)
        self.assertEqual(response["page"], 3)
        self.assertEqual(response["page_size"], 10)
        self.assertEqual(response["total_pages"], 3)
        self.assertEqual(db.queries[1]._offset_clause.value, 20)
        self.assertEqual(db.queries[1]._limit_clause.value, 10)

    async def test_limit_alias_overrides_default_page_size(self):
        db = _Session([_Result(scalar_value=0), _Result()])

        response = await get_deye_alarm_events(
            object_id="object-id",
            page=2,
            page_size=100,
            limit=25,
            db=db,
        )

        self.assertEqual(response["page_size"], 25)
        self.assertEqual(response["total_pages"], 0)
        self.assertEqual(db.queries[1]._offset_clause.value, 25)
        self.assertEqual(db.queries[1]._limit_clause.value, 25)
