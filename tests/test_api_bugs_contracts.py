import unittest
from dataclasses import dataclass
from datetime import time
import os
from types import SimpleNamespace

os.environ["DEBUG"] = "false"

from backend.database.models import EnergeticDevice, EnergeticObject, User
from backend.repository.energy.energetic_object_response import build_object_response
from backend.schemas.energetic_schedule import EnergeticScheduleResponse
from backend.schemas.external_auth import InitiateLoginResponse
from backend.schemas.user import ResponseCorIdModel
from backend.services.shared.corid_deep_link import build_corid_mobile_deep_link
from backend.services.shared.websocket_auth import extract_websocket_bearer_token


@dataclass
class _FakeScalars:
    values: list

    def all(self):
        return self.values


class _FakeExecuteResult:
    def __init__(self, scalar_value=None, scalars_values=None):
        self._scalar_value = scalar_value
        self._scalars_values = scalars_values or []

    def scalar_one_or_none(self):
        return self._scalar_value

    def scalars(self):
        return _FakeScalars(self._scalars_values)


class _FakeAsyncSession:
    def __init__(self, execute_results):
        self._execute_results = list(execute_results)

    async def execute(self, _query):
        if not self._execute_results:
            raise AssertionError("Unexpected execute call")
        return self._execute_results.pop(0)


class ApiBugsContractTests(unittest.IsolatedAsyncioTestCase):
    def test_my_core_id_response_is_object(self):
        response = ResponseCorIdModel(cor_id="29FX141CZ-2002M")

        self.assertEqual(response.model_dump(), {"cor_id": "29FX141CZ-2002M"})

    def test_initiate_login_response_keeps_corid_fields(self):
        response = InitiateLoginResponse(
            session_token="local-token",
            authorize_url="https://corid.example/auth/confirm",
            deep_link="coridapp://open?sessionToken=corid-token",
            corid_session_token="corid-token",
        )

        self.assertEqual(
            response.model_dump()["deep_link"],
            "coridapp://open?sessionToken=corid-token",
        )
        self.assertEqual(response.model_dump()["corid_session_token"], "corid-token")

    def test_corid_mobile_deep_link_uses_app_scheme(self):
        deep_link = build_corid_mobile_deep_link(
            "corid-token",
            email="user@example.com",
        )

        self.assertEqual(
            deep_link,
            "coridapp://open?sessionToken=corid-token&email=user%40example.com",
        )

    async def test_shared_object_response_masks_secret_and_normalizes_bridge(self):
        db_device_id = "424a5a63-a331-41af-89a7-6f25a7bb7bd7"
        object_owner_cor_id = "owner-cor-id"
        energetic_object = EnergeticObject(
            id="object-id",
            name="Server room",
            owner_cor_id=None,
            timezone="Europe/Kyiv",
            cor_bridges=[db_device_id],
            slave_ids=[],
            telegram_bot_token="secret-token",
        )
        current_user = User(
            id="reader-id",
            email="reader@example.com",
            cor_id="reader-cor-id",
            password="x",
            unique_cipher_key="x",
        )
        current_user.user_roles = []
        db = _FakeAsyncSession(
            [
                _FakeExecuteResult(scalar_value=object_owner_cor_id),
                _FakeExecuteResult(
                    scalars_values=[
                        EnergeticDevice(
                            id=db_device_id,
                            device_id="COR-70B8F662B424",
                            owner_cor_id=object_owner_cor_id,
                        )
                    ]
                ),
            ]
        )

        response = await build_object_response(db, energetic_object, current_user)

        self.assertEqual(response.owner_cor_id, object_owner_cor_id)
        self.assertEqual(response.cor_bridges, ["COR-70B8F662B424"])
        self.assertIsNone(response.telegram_bot_token)

    def test_schedule_response_grid_feed_is_int(self):
        response = EnergeticScheduleResponse(
            id="schedule-id",
            start_time=time(12, 30),
            grid_feed_w=500.0,
            battery_level_percent=80,
            charge_battery_value=20,
        )

        self.assertEqual(response.grid_feed_w, 500)
        self.assertIs(type(response.grid_feed_w), int)

    def test_websocket_bearer_token_can_be_read_from_header_or_query(self):
        header_websocket = SimpleNamespace(
            headers={"authorization": "Bearer header-token"},
            query_params={},
        )
        query_websocket = SimpleNamespace(
            headers={},
            query_params={"access_token": "query-token"},
        )

        self.assertEqual(
            extract_websocket_bearer_token(header_websocket),
            "header-token",
        )
        self.assertEqual(extract_websocket_bearer_token(query_websocket), "query-token")


if __name__ == "__main__":
    unittest.main()
