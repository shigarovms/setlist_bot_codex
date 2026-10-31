import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def public_base_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Calendar public base URL must be an HTTPS origin without credentials")
    return value.rstrip("/")


class CalendarSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CALENDAR_", env_file=None, extra="ignore")

    database_url: str
    expected_bot_id: int = Field(gt=0)
    environment: Literal["sandbox", "production"]
    bot_username: str
    host: str = "0.0.0.0"
    port: int = Field(default=8090, ge=1, le=65535)

    @field_validator("bot_username")
    @classmethod
    def valid_username(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", value):
            raise ValueError("Invalid bot username")
        return value
