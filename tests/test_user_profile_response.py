import base64
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from backend.routes.user import person as person_routes
from backend.services.user.cipher import encrypt_data


class UserProfileResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_profile_response_skips_corrupted_encrypted_field(self):
        key = b"1234567890123456"
        encrypted_first_name = await encrypt_data(b"John", key)
        aes_key_without_padding = base64.urlsafe_b64encode(key).decode().rstrip("=")
        db_profile = SimpleNamespace(
            id="profile-id",
            user_id="user-id",
            encrypted_surname=b"bad",
            encrypted_first_name=encrypted_first_name,
            encrypted_middle_name=None,
            birth_date=date(1990, 1, 1),
            phone_number=None,
            city="Kyiv",
            car_brand=None,
            engine_type=None,
            fuel_tank_volume=None,
            photo_data=None,
            photo_file_type=None,
            change_date=None,
            create_date=None,
        )
        current_user = SimpleNamespace(
            id="user-id",
            email="user@example.com",
            user_sex="M",
        )

        with patch.object(person_routes.settings, "aes_key", aes_key_without_padding):
            response = await person_routes._create_profile_response(
                db_profile,
                current_user,
                person_routes.router,
            )

        self.assertEqual(response.first_name, "John")
        self.assertIsNone(response.surname)
        self.assertEqual(response.email, "user@example.com")


if __name__ == "__main__":
    unittest.main()
