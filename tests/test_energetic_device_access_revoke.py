import unittest
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException

from backend.database.models.energy import EnergeticDevice, EnergeticDeviceAccess
from backend.database.models.enums import AccessLevel
from backend.repository.energy.energetic_device import delete_device_access


@dataclass
class _FakeScalars:
    values: list[Any]

    def all(self):
        return self.values


class _FakeExecuteResult:
    def __init__(self, scalar_value: Optional[Any] = None, scalars_values: Optional[list[Any]] = None):
        self._scalar_value = scalar_value
        self._scalars_values = scalars_values or []

    def scalar_one_or_none(self):
        return self._scalar_value

    def scalars(self):
        return _FakeScalars(self._scalars_values)


class _FakeAsyncSession:
    def __init__(self, execute_results: list[_FakeExecuteResult]):
        self._execute_results = execute_results
        self._execute_idx = 0
        self.deleted: list[Any] = []
        self.committed = False

    async def execute(self, _query):
        if self._execute_idx >= len(self._execute_results):
            raise AssertionError("Unexpected execute call")
        result = self._execute_results[self._execute_idx]
        self._execute_idx += 1
        return result

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True


class EnergeticDeviceAccessRevokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_device_access_success_for_owner(self):
        device = EnergeticDevice(
            id="dev-1",
            device_id="COR-DEVICE-1",
            owner_cor_id="owner-cor-id",
        )
        access = EnergeticDeviceAccess(
            id="acc-1",
            device_id="dev-1",
            accessing_user_cor_id="target-user",
            granting_user_cor_id="owner-cor-id",
            access_level=AccessLevel.READ,
        )

        db = _FakeAsyncSession(
            execute_results=[
                _FakeExecuteResult(scalar_value=device),
                _FakeExecuteResult(scalar_value=access),
            ]
        )

        await delete_device_access(
            db,
            device_id="dev-1",
            accessing_user_cor_id="target-user",
            owner_cor_id="owner-cor-id",
        )

        self.assertTrue(db.committed)
        self.assertIn(access, db.deleted)

    async def test_delete_device_access_forbidden_for_non_owner(self):
        device = EnergeticDevice(
            id="dev-1",
            device_id="COR-DEVICE-1",
            owner_cor_id="real-owner",
        )

        db = _FakeAsyncSession(
            execute_results=[
                _FakeExecuteResult(scalar_value=device),
            ]
        )

        with self.assertRaises(HTTPException) as exc:
            await delete_device_access(
                db,
                device_id="dev-1",
                accessing_user_cor_id="target-user",
                owner_cor_id="other-user",
            )

        self.assertEqual(exc.exception.status_code, 403)
        self.assertFalse(db.committed)
        self.assertEqual(db.deleted, [])

    async def test_delete_device_access_not_found(self):
        device = EnergeticDevice(
            id="dev-1",
            device_id="COR-DEVICE-1",
            owner_cor_id="owner-cor-id",
        )

        db = _FakeAsyncSession(
            execute_results=[
                _FakeExecuteResult(scalar_value=device),
                _FakeExecuteResult(scalar_value=None),
            ]
        )

        with self.assertRaises(HTTPException) as exc:
            await delete_device_access(
                db,
                device_id="dev-1",
                accessing_user_cor_id="target-user",
                owner_cor_id="owner-cor-id",
            )

        self.assertEqual(exc.exception.status_code, 404)
        self.assertFalse(db.committed)
        self.assertEqual(db.deleted, [])


if __name__ == "__main__":
    unittest.main()
