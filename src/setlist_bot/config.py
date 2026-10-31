from typing import Literal

from aiogram.types import BotCommandScopeUnion
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from setlist_bot.calendar.settings import public_base_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    bot_token: SecretStr | None = None
    expected_bot_id: int | None = Field(default=None, gt=0)
    runtime_environment: Literal["development", "sandbox", "production"] = "development"
    runtime_location: str = Field(default="local", min_length=1, max_length=100)
    telegram_diagnostics: bool = False
    calendar_public_base_url: str = ""

    @field_validator("calendar_public_base_url")
    @classmethod
    def validate_calendar_url(cls, value: str) -> str:
        return public_base_url(value)

    bot_command_legacy_languages: list[str] = Field(default_factory=list)
    bot_command_extra_scopes: list[BotCommandScopeUnion] = Field(default_factory=list)

    @field_validator("bot_command_legacy_languages")
    @classmethod
    def validate_command_languages(cls, values: list[str]) -> list[str]:
        if any(
            len(value) != 2 or not value.isascii() or not value.islower() or not value.isalpha()
            for value in values
        ):
            raise ValueError("Command languages must be lowercase two-letter language codes")
        return sorted(set(values))
