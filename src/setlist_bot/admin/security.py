"""RU: Пароли, сессии и подписи форм. EN: No credentials ever belong in browser URLs."""

import hashlib
import hmac
import re
import secrets
import time
from datetime import timedelta
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

SESSION_LIFETIME = timedelta(hours=8)
COOKIE = "setlist_admin"
LOGIN_COOKIE = "setlist_admin_login"


class AdminSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WEB_ADMIN_", env_file=".env.admin", extra="ignore"
    )
    database_url: SecretStr
    username: str = "admin"
    password_hash: SecretStr
    secret: SecretStr
    allow_production: bool = False
    secure_cookie: bool = False
    host: str = "127.0.0.1"
    port: int = 8080
    allowed_hosts: str = "localhost,127.0.0.1"
    host_report: str = "/app/.admin-status/host.json"
    production_database_url: SecretStr | None = None
    production_read_only: bool = True
    read_only: bool = False
    contour: Literal["sandbox", "production"] = "sandbox"
    url_prefix: str = ""
    console_switch: bool = False
    production_available: bool = False

    @field_validator("secret")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("WEB_ADMIN_SECRET must contain at least 32 characters")
        return value

    @field_validator("username")
    @classmethod
    def valid_username(cls, value: str) -> str:
        if (
            not 1 <= len(value) <= 100
            or not value.isascii()
            or not value.replace("_", "").replace("-", "").isalnum()
        ):
            raise ValueError("Use 1–100 ASCII letters, digits, underscore or dash")
        return value

    @field_validator("password_hash")
    @classmethod
    def valid_hash(cls, value: SecretStr) -> SecretStr:
        parts = value.get_secret_value().split(":")
        if len(parts) != 3 or parts[0] != "scrypt" or len(parts[1]) != 32 or len(parts[2]) != 128:
            raise ValueError("Generate the password hash using the admin configuration command")
        try:
            bytes.fromhex(parts[1] + parts[2])
        except ValueError as exc:
            raise ValueError("Invalid scrypt encoding") from exc
        return value

    def checked_url(self) -> str:
        url = self.database_url.get_secret_value()
        parsed = make_url(url)
        if parsed.drivername != "postgresql+asyncpg":
            raise ValueError("Use PostgreSQL with the asyncpg driver")
        name = parsed.database or ""
        if not self.allow_production and not name.endswith(("_sandbox", "_test")):
            raise ValueError(
                "Refusing a non-sandbox database without WEB_ADMIN_ALLOW_PRODUCTION=true"
            )
        return url


def password_hash(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("Use a password between 12 and 256 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return f"scrypt:{salt}:{digest.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    if len(password) > 256:
        return False
    _, salt, expected = encoded.split(":")
    result = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return hmac.compare_digest(result.hex(), expected)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sign(settings: AdminSettings, value: str) -> str:
    return hmac.new(
        settings.secret.get_secret_value().encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def form_proof(settings: AdminSettings, session: str, action: str, target: str, nonce: str) -> str:
    return sign(settings, f"form:{session}:{action}:{target}:{nonce}")


def login_token(settings: AdminSettings) -> str:
    value = f"{int(time.time())}:{secrets.token_hex(16)}"
    return value + ":" + sign(settings, "login:" + value)


def valid_login_token(settings: AdminSettings, value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 3 or not re.fullmatch("[a-f0-9]{64}", parts[2]):
        return False
    try:
        age = time.time() - int(parts[0])
    except ValueError:
        return False
    return 0 <= age <= 900 and hmac.compare_digest(
        parts[2], sign(settings, "login:" + ":".join(parts[:2]))
    )
