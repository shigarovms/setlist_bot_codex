from aiogram.types import BotCommandScopeUnion
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    bot_token: SecretStr | None = None
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
