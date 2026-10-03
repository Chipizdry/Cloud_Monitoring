import unittest
from dataclasses import dataclass
from typing import Any, Optional

from backend.database.models.energy import EnergeticDevice, EnergeticDeviceAccess, EnergeticObject, EnergeticObjectAccess
from backend.database.models.enums import AccessLevel
from backend.repository.energy.energetic_object_access import revoke_object_access, upsert_object_access


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
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.committed = False
        self.refreshed = False

    async def execute(self, _query):
        if self._execute_idx >= len(self._execute_results):
            raise AssertionError("Unexpected execute call")
        result = self._execute_results[self._execute_idx]
        self._execute_idx += 1
        return result

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        return None

    async def refresh(self, _obj):
        self.refreshed = True


class EnergeticObjectCorBridgesSharingTests(unittest.IsolatedAsyncioTestCase):
    async def test_share_object_cascades_access_to_cor_bridges(self):
        energetic_object = EnergeticObject(
            id="obj-1",
            name="test-object",
            owner_cor_id="owner-cor-id",
            cor_bridges=["bridge-1", "bridge-2"],
        )

        bridge_device_1 = EnergeticDevice(id="dev-1", device_id="bridge-1", owner_cor_id="owner-cor-id")
        bridge_device_2 = EnergeticDevice(id="dev-2", device_id="bridge-2", owner_cor_id="owner-cor-id")

        existing_bridge_access = EnergeticDeviceAccess(
            id="acc-dev-2",
            device_id="dev-2",
            accessing_user_cor_id="target-user",
            granting_user_cor_id="old-grantor",
            access_level=AccessLevel.READ,
        )

        db = _FakeAsyncSession(
            execute_results=[
                _FakeExecuteResult(scalar_value=energetic_object),
                _FakeExecuteResult(scalar_value=None),
                _FakeExecuteResult(scalars_values=[bridge_device_1, bridge_device_2]),
                _FakeExecuteResult(scalar_value=None),
                _FakeExecuteResult(scalar_value=existing_bridge_access),
            ]
        )

        access = await upsert_object_access(
            db,
            energetic_object_id="obj-1",
            accessing_user_cor_id="target-user",
            access_level=AccessLevel.SHARE,
            granting_user_cor_id="owner-cor-id",
        )

        self.assertTrue(db.committed)
        self.assertTrue(db.refreshed)
        self.assertEqual(access.access_level, AccessLevel.SHARE)
        self.assertEqual(access.granting_user_cor_id, "owner-cor-id")

        added_object_access = [obj for obj in db.added if isinstance(obj, EnergeticObjectAccess)]
        added_device_access = [obj for obj in db.added if isinstance(obj, EnergeticDeviceAccess)]

        self.assertEqual(len(added_object_access), 1)
        self.assertEqual(len(added_device_access), 1)
        self.assertEqual(added_device_access[0].device_id, "dev-1")
        self.assertEqual(added_device_access[0].access_level, AccessLevel.READ)
        self.assertEqual(added_device_access[0].accessing_user_cor_id, "target-user")

        self.assertEqual(existing_bridge_access.access_level, AccessLevel.READ)
        self.assertEqual(existing_bridge_access.granting_user_cor_id, "owner-cor-id")

    async def test_revoke_object_access_revokes_cor_bridges_with_same_grantor(self):
        energetic_object = EnergeticObject(
            id="obj-1",
            name="test-object",
            owner_cor_id="owner-cor-id",
            cor_bridges=["bridge-1", "bridge-2"],
        )

        object_access = EnergeticObjectAccess(
            id="obj-acc",
            energetic_object_id="obj-1",
            accessing_user_cor_id="target-user",
            granting_user_cor_id="owner-cor-id",
            access_level=AccessLevel.READ_WRITE,
        )

        bridge_device_1 = EnergeticDevice(id="dev-1", device_id="bridge-1", owner_cor_id="owner-cor-id")
        bridge_device_2 = EnergeticDevice(id="dev-2", device_id="bridge-2", owner_cor_id="owner-cor-id")

        bridge_access_same_grantor = EnergeticDeviceAccess(
            id="dev-acc-1",
            device_id="dev-1",
            accessing_user_cor_id="target-user",
            granting_user_cor_id="owner-cor-id",
            access_level=AccessLevel.READ_WRITE,
        )
        bridge_access_other_grantor = EnergeticDeviceAccess(
            id="dev-acc-2",
            device_id="dev-2",
            accessing_user_cor_id="target-user",
            granting_user_cor_id="someone-else",
            access_level=AccessLevel.READ_WRITE,
        )

        db = _FakeAsyncSession(
            execute_results=[
                _FakeExecuteResult(scalar_value=energetic_object),
                _FakeExecuteResult(scalar_value=object_access),
                _FakeExecuteResult(scalars_values=[bridge_device_1, bridge_device_2]),
                _FakeExecuteResult(scalar_value=bridge_access_same_grantor),
                _FakeExecuteResult(scalar_value=bridge_access_other_grantor),
            ]
        )

        await revoke_object_access(
            db,
            energetic_object_id="obj-1",
            accessing_user_cor_id="target-user",
        )

        self.assertTrue(db.committed)
        self.assertIn(object_access, db.deleted)
        self.assertIn(bridge_access_same_grantor, db.deleted)
        self.assertNotIn(bridge_access_other_grantor, db.deleted)


if __name__ == "__main__":
    unittest.main()
