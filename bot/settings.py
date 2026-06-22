from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str = Field(..., min_length=10)
    admin_ids: list[int] = Field(default_factory=list)

    webhook_base_url: str = ""
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    webhook_secret: str = Field(..., min_length=8)

    default_lang: str = "zh"
    tz: str = "Asia/Shanghai"

    turnstile_enabled: bool = False
    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""

    database_url: str = "sqlite+aiosqlite:///./data/bot.db"

    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v):
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v

    @field_validator("webhook_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def webhook_path(self) -> str:
        return f"/tg/{self.webhook_secret}"

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_base_url}{self.webhook_path}"

    @property
    def turnstile_active(self) -> bool:
        return (
            self.turnstile_enabled
            and bool(self.turnstile_site_key)
            and bool(self.turnstile_secret_key)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
