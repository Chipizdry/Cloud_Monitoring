from pydantic_settings import BaseSettings
from pydantic import Field
import glob
from typing import List, Optional


class Settings(BaseSettings):
    # Database
    sqlalchemy_database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/postgres",
        alias="SQLALCHEMY_DATABASE_URL",
    )
    postgres_password: str = Field(default="postgres", alias="POSTGRES_PASSWORD")
    postgres_user: str = Field(default="postgres", alias="POSTGRES_USER")
    postgres_db: str = Field(default="postgres", alias="POSTGRES_DB")

    # Security / Auth
    algorithm: str = Field(default="HS256", alias="ALGORITHM")
    secret_key: str = Field(default="", alias="SECRET_KEY")
    access_token_expiration: int = Field(default=3600, alias="ACCESS_TOKEN_EXPIRATION")
    refresh_token_expiration: int = Field(default=168, alias="REFRESH_TOKEN_EXPIRATION")

    # App
    app_env: str = Field(default="development", alias="APP_ENV")
    debug: bool = Field(default=False, alias="DEBUG")
    reload: bool = Field(default=False, alias="RELOAD")
    docs_enabled: bool = Field(default=True, alias="DOCS_ENABLED")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    corfuel_base_url: str = Field(default="", alias="CORFUEL_BASE_URL")

    # Crypto
    aes_key: str = Field(default="", alias="AES_KEY")

    # Email
    mail_username: str = Field(default="", alias="MAIL_USERNAME")
    mail_password: str = Field(default="", alias="MAIL_PASSWORD")
    mail_from: str = Field(default="Cor.Auth@EXAMPLE.COM", alias="MAIL_FROM")
    mail_port: int = Field(default=465, alias="MAIL_PORT")
    mail_server: str = Field(default="", alias="MAIL_SERVER")
    marketing_email: Optional[str] = Field(default=None, alias="MARKETING_EMAIL")

    # Redis
    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")

    # COR-ID parameters
    corid_facility_key: int = Field(default=1, alias="CORID_FACILITY_KEY")
    corid_version: int = Field(default=0, alias="CORID_VERSION")
    corid_version_bit: int = Field(default=1, alias="CORID_VERSION_BIT")
    corid_days_since_bit: int = Field(default=16, alias="CORID_DAYS_SINCE_BIT")
    corid_facility_bit: int = Field(default=16, alias="CORID_FACILITY_BIT")
    corid_patient_bit: int = Field(default=16, alias="CORID_PATIENT_BIT")
    corid_charset: str = Field(
        default="0123456789ABCDEFGHJKLMNPRSTUVWXYZ", alias="CORID_CHARSET"
    )
    # Accounts / Access control
    superadmin_emails: List[str] = Field(
        default_factory=list, alias="SUPERADMIN_EMAILS"
    )  # Superadmin с полными правами
    allowed_cors_origins: List[str] = Field(
        default_factory=list, alias="ALLOWED_CORS_ORIGINS"
    )
    allowed_hosts: List[str] = Field(default_factory=list, alias="ALLOWED_HOSTS")


    # Cor-ID OAuth (Authorization Code + PKCE)
    corid_base_url: str = Field(default="", alias="CORID_BASE_URL")
    corid_client_id: str = Field(default="", alias="CORID_CLIENT_ID")
    corid_client_secret: str = Field(default="", alias="CORID_CLIENT_SECRET")
    corid_redirect_uri: Optional[str] = Field(
        default=None, alias="CORID_REDIRECT_URI"
    )

    # Internal API key for Cor-ID identity registry (service-to-service auth)
    corid_internal_api_key: str = Field(
        default="", alias="CORID_INTERNAL_API_KEY"
    )
    
    # Energy System / Telegram Bot
    telegram_bot_token: Optional[str] = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    websocket_port: int = Field(default=45762, alias="WEBSOCKET_PORT")
    
    class Config:

        env_files = glob.glob("./*.env")
        env_file = env_files[0] if env_files else ".env"
        env_file_encoding: str = "utf-8"
        extra = "ignore"


settings = Settings()
