import base64

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

from setlist_bot.calendar.settings import CalendarSettings, public_base_url


class MiniAppSettings(CalendarSettings):
    model_config = SettingsConfigDict(env_prefix="MINIAPP_", env_file=None, extra="ignore")
    port: int = Field(default=8091, ge=1, le=65535)
    auth_key: SecretStr
    public_base_url: str

    @field_validator("public_base_url")
    @classmethod
    def valid_origin(cls, value: str) -> str:
        result = public_base_url(value)
        if not result:
            raise ValueError("Mini App needs a public HTTPS origin")
        return result

    @field_validator("auth_key")
    @classmethod
    def valid_key(cls, value: SecretStr) -> SecretStr:
        try:
            if len(base64.b64decode(value.get_secret_value(), altchars=b"-_", validate=True)) != 32:
                raise ValueError()
        except ValueError:
            raise ValueError("Invalid Mini App validation key") from None
        return value
