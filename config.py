from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.utils.token import TokenValidationError, validate_token
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from services.fair_queue import DEFAULT_ALPHA, DEFAULT_FAIRNESS_POWER


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: SecretStr
    bot_timezone: str
    database_path: Path = Path("data/queue.sqlite3")
    pin_event_messages: bool = False
    fairness_power: float = Field(DEFAULT_FAIRNESS_POWER, ge=0, allow_inf_nan=False)
    alpha: float = Field(DEFAULT_ALPHA, ge=0, le=1)

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, value: SecretStr) -> SecretStr:
        try:
            validate_token(value.get_secret_value())
        except TokenValidationError as exc:
            raise ValueError("Укажите токен, выданный @BotFather") from exc
        return value

    @field_validator("bot_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Укажите часовой пояс из базы IANA") from exc
        return value

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.bot_timezone)
