"""
Генератор секретных ключей для конфигурации COR-ID

Использование:
        python secret_generator.py [--all] [--jwt] [--aes] [--totp] [--postgres]

Примеры:
        python secret_generator.py --all          # Генерирует все ключи
        python secret_generator.py --jwt --aes    # Генерирует только JWT и AES ключи
        python secret_generator.py                # Генерирует только TOTP
"""

import secrets
import base64
import pyotp
import argparse
from cryptography.fernet import Fernet


def generate_jwt_secret_key(length: int = 64) -> str:
    """
    Генерирует SECRET_KEY для JWT токенов (HS256)

    Args:
            length: Длина ключа в байтах (по умолчанию 64 = 128 hex символов)

    Returns:
            Hex строка для использования в SECRET_KEY
    """
    return secrets.token_hex(length)


def generate_aes_key() -> str:
    """
    Генерирует AES_KEY для шифрования данных (Fernet)

    Returns:
            Base64-encoded ключ для Fernet шифрования
    """
    return Fernet.generate_key().decode("utf-8")


def generate_totp_secret() -> str:
    """
    Генерирует TOTP секрет для двухфакторной аутентификации

    Returns:
            Base32-encoded секрет для TOTP
    """
    return pyotp.random_base32()


def generate_postgres_password(length: int = 32) -> str:
    """
    Генерирует безопасный пароль для PostgreSQL

    Args:
            length: Длина пароля

    Returns:
            URL-safe пароль (без специальных символов для совместимости с connection strings)
    """
    return secrets.token_urlsafe(length)[:length]


def print_env_format(key: str, value: str, comment: str = ""):
    """Печатает в формате .env файла"""
    if comment:
        print(f"# {comment}")
    print(f"{key}={value}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Генератор секретных ключей для COR-ID",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  %(prog)s --all                    Генерирует все ключи
  %(prog)s --jwt --aes              Генерирует JWT и AES ключи
  %(prog)s --totp                   Генерирует только TOTP секрет
  %(prog)s --postgres               Генерирует пароль PostgreSQL
		""",
    )

    parser.add_argument("--all", action="store_true", help="Генерировать все ключи")
    parser.add_argument(
        "--jwt", action="store_true", help="Генерировать JWT SECRET_KEY"
    )
    parser.add_argument(
        "--aes", action="store_true", help="Генерировать AES_KEY для шифрования"
    )
    parser.add_argument("--totp", action="store_true", help="Генерировать TOTP секрет")
    parser.add_argument(
        "--postgres", action="store_true", help="Генерировать пароль PostgreSQL"
    )

    args = parser.parse_args()

    if not any([args.all, args.jwt, args.aes, args.totp, args.postgres]):
        args.totp = True

    print("=" * 80)
    print("🔐 ГЕНЕРАТОР СЕКРЕТНЫХ КЛЮЧЕЙ COR-ID")
    print("=" * 80)
    print()

    if args.all or args.jwt:
        jwt_key = generate_jwt_secret_key()
        print_env_format(
            "SECRET_KEY", jwt_key, "JWT Secret Key для подписи токенов (HS256)"
        )

    if args.all or args.aes:
        aes_key = generate_aes_key()
        print_env_format("AES_KEY", aes_key, "AES Key для шифрования данных (Fernet)")

    if args.all or args.totp:
        totp_secret = generate_totp_secret()
        print_env_format(
            "FUEL_STATION_TOTP_SECRET",
            totp_secret,
            "TOTP Secret для офлайн авторизации заправочных станций",
        )

    if args.all or args.postgres:
        postgres_password = generate_postgres_password()
        print_env_format(
            "POSTGRES_PASSWORD", postgres_password, "Пароль для PostgreSQL"
        )
        print("# SQLALCHEMY_DATABASE_URL:")
        print(
            f"# SQLALCHEMY_DATABASE_URL=postgresql+asyncpg://postgres:{postgres_password}@postgres:5432/auth_db"
        )
        print()

    print("=" * 80)
    print("✅ ГОТОВО!")
    print("=" * 80)
    print()


if __name__ == "__main__":
    main()
