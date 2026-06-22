from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from bot.i18n import load_locales
from bot.settings import get_settings

log = logging.getLogger("bot")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def _banner(settings) -> None:
    log.info("=" * 60)
    log.info(" tg-giveaway-bot starting")
    log.info(" lang=%s tz=%s turnstile=%s db=%s",
             settings.default_lang, settings.tz,
             "on" if settings.turnstile_active else "off",
             settings.database_url.split("://", 1)[0])
    log.info(" admins=%s", settings.admin_ids or "(none)")
    log.info(" webhook=%s", settings.webhook_url or "(unset)")
    log.info("=" * 60)


async def main() -> None:
    settings = get_settings()
    _setup_logging(settings.log_level)
    _banner(settings)

    load_locales(settings.default_lang)

    from bot.db.base import init_db
    from bot.handlers import build_router
    from bot.services.scheduler import GiveawayScheduler
    from bot.web.routes import register_web_routes
    from bot.web.webhook import register_webhook

    await init_db(settings.database_url)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(build_router())

    scheduler = GiveawayScheduler(bot=bot, tz=settings.tz)
    dp["scheduler"] = scheduler

    app = web.Application()
    app["bot"] = bot
    app["dp"] = dp
    app["settings"] = settings
    app["scheduler"] = scheduler

    if settings.webhook_base_url:
        register_webhook(app, dp, bot, settings)
    register_web_routes(app)

    async def on_startup(_app: web.Application) -> None:
        scheduler.start()
        await scheduler.restore_from_db()
        if settings.webhook_base_url:
            await bot.set_webhook(
                url=settings.webhook_url,
                secret_token=settings.webhook_secret,
                drop_pending_updates=True,
            )
            log.info("webhook registered → %s", settings.webhook_url)
        else:
            log.info("WEBHOOK_BASE_URL is empty — starting in polling mode.")

    async def on_shutdown(_app: web.Application) -> None:
        if settings.webhook_base_url:
            try:
                await bot.delete_webhook()
            except Exception:
                log.exception("delete_webhook failed")
        scheduler.shutdown()
        await bot.session.close()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=settings.web_host, port=settings.web_port)
    await site.start()
    log.info("listening on %s:%d", settings.web_host, settings.web_port)

    if settings.webhook_base_url:
        stop = asyncio.Event()
        try:
            await stop.wait()
        finally:
            await runner.cleanup()
    else:
        try:
            await dp.start_polling(bot)
        finally:
            await runner.cleanup()
