from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    site_name: str = "FastAPI Blog"

    database_url: str

    secret_key: SecretStr
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    cookie_secure: bool = False  # Set True in production (HTTPS only)

    max_upload_size_bytes: int = 5 * 1024 * 1024

    posts_per_page: int = 10

    reset_token_expire_minutes: int = 60
    verification_token_expire_hours: int = 48
    require_email_verification: bool = False

    rate_limit_enabled: bool = True

    mail_backend: Literal["smtp", "console"] = "smtp"
    mail_server: str = "localhost"
    mail_port: int = 587
    mail_username: str = ""
    mail_password: SecretStr = SecretStr("")
    mail_from: str = "noreply@example.com"
    mail_use_tls: bool = True

    frontend_url: str = "http://localhost:8000"


settings = Settings()  # type: ignore[call-arg] # Loaded from .env file
