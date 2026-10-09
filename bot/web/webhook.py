from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)
from aiohttp import web

from bot.settings import Settings

log = logging.getLogger("bot.web.webhook")


def register_webhook(
    app: web.Application,
    dp: Dispatcher,
    bot: Bot,
    settings: Settings,
) -> None:
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret,
    ).register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)
    log.info("webhook handler registered: %s", settings.masked_webhook_url())
