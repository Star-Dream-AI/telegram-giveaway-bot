from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str = Field(..., min_length=10)
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    webhook_base_url: str = ""
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    # Optional on purpose.  Long polling is the documented default path, but
    # this field used to be "Field(..., min_length=8)", so a perfectly valid
    # long-polling deployment died at import time with a pydantic
    # ValidationError demanding a webhook secret it had no use for.  It is now
    # enforced only in webhook mode (see the model validator below).
    webhook_secret: str = ""

    default_lang: str = "zh"
    tz: str = "Asia/Shanghai"

    turnstile_enabled: bool = False
    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""
    # Public origin used to build the verification link handed to users.  When
    # empty the bot falls back to WEBHOOK_BASE_URL; if that is empty too, a
    # captcha-gated giveaway is refused at creation time rather than producing
    # a link nobody can open.
    public_base_url: str = ""

    database_url: str = "sqlite+aiosqlite:///./data/bot.db"

    log_level: str = "INFO"
    # drop_pending_updates=True on every start silently discarded queued
    # updates after each deploy - including the giveaway post an admin had
    # literally just created.  Off by default; opt in when you are certain.
    drop_pending_updates: bool = False

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v):
        if isinstance(v, str):
            if v.strip().startswith("["):
                return json.loads(v)
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v

    @field_validator("webhook_base_url", "public_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @model_validator(mode="after")
    def _webhook_requires_secret(self) -> "Settings":
        if self.webhook_base_url and len(self.webhook_secret) < 8:
            raise ValueError(
                "WEBHOOK_SECRET must be set to at least 8 characters when "
                "WEBHOOK_BASE_URL is configured"
            )
        return self

    @property
    def webhook_mode(self) -> bool:
        return bool(self.webhook_base_url)

    @property
    def public_origin(self) -> str:
        """Origin a participant can actually reach, for building links."""
        return self.public_base_url or self.webhook_base_url

    @property
    def webhook_path(self) -> str:
        return f"/tg/{quote(self.webhook_secret or 'unset', safe='')}"

    @property
    def webhook_url(self) -> str:
        if not self.webhook_base_url:
            return ""
        return f"{self.webhook_base_url}{self.webhook_path}"

    @property
    def turnstile_active(self) -> bool:
        return (
            self.turnstile_enabled
            and bool(self.turnstile_site_key)
            and bool(self.turnstile_secret_key)
        )

    def turnstile_capable(self) -> bool:
        """True when a captcha-gated giveaway could actually be completed."""
        return self.turnstile_active and bool(self.public_origin)

    def verify_url(self, giveaway_id: int, user_id: int) -> str:
        """Challenge URL handed to a participant."""
        return f"{self.public_origin}/verify/{giveaway_id}?uid={int(user_id)}"

    def masked_webhook_url(self) -> str:
        """Webhook URL safe for logs - the secret is a bearer credential."""
        if not self.webhook_base_url:
            return "(unset)"
        if not self.webhook_secret:
            return f"{self.webhook_base_url}/tg/<no-secret>"
        head = self.webhook_secret[:2]
        tail = self.webhook_secret[-2:]
        return f"{self.webhook_base_url}/tg/{head}***{tail}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
