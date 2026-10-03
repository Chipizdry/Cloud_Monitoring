import unittest

from backend.services.user.cipher import (
    decode_base64_with_padding,
    decrypt_data,
    encrypt_data,
)


class CipherBase64PaddingTests(unittest.IsolatedAsyncioTestCase):
    def test_decode_base64_with_padding_restores_missing_padding(self):
        self.assertEqual(decode_base64_with_padding("MTI"), b"12")

    async def test_decrypt_data_accepts_ciphertext_without_base64_padding(self):
        key = b"1234567890123456"
        encrypted = await encrypt_data(b"profile-field", key)
        encrypted_without_padding = encrypted.rstrip(b"=")

        decrypted = await decrypt_data(encrypted_without_padding, key)

        self.assertEqual(decrypted, "profile-field")

    async def test_decrypt_data_wraps_invalid_base64_as_value_error(self):
        with self.assertRaisesRegex(ValueError, "Decryption failed"):
            await decrypt_data(b"not-valid-base64", b"1234567890123456")


if __name__ == "__main__":
    unittest.main()
