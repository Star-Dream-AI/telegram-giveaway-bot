from __future__ import annotations

import asyncio
import logging
import signal
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
    log.info(
        " lang=%s tz=%s turnstile=%s db=%s",
        settings.default_lang,
        settings.tz,
        "on" if settings.turnstile_active else "off",
        settings.database_url.split("://", 1)[0],
    )
    log.info(" admins=%s", settings.admin_ids or "(none)")
    # The webhook path embeds WEBHOOK_SECRET; logging the full URL would
    # write the secret into log files.  masked_webhook_url() keeps just
    # enough to tell operators which secret is in use.
    log.info(" webhook=%s", settings.masked_webhook_url())
    log.info("=" * 60)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """Bridge SIGINT/SIGTERM onto ``stop`` so shutdown hooks actually run."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            # Windows has no loop signal handlers; KeyboardInterrupt still
            # unwinds through the finally block, SIGTERM needs a fallback.
            try:
                signal.signal(sig, lambda *_: stop.set())
            except (OSError, ValueError):
                log.debug("cannot install handler for %s", sig)


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

    # The verification page builds t.me deep links, which need the bot's
    # own username; fetch it once so /verify can offer a return button.
    from bot.services import turnstile as ts

    if settings.turnstile_active:
        try:
            me = await bot.get_me()
            ts.set_bot_username(me.username)
            log.info("turnstile active, bot=@%s", me.username)
        except Exception:
            log.exception("could not read bot username; verify links will omit the button")

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
        await _configure_delivery(bot, settings)

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

    runner = web.AppRunner(app, access_log=None)
    try:
        await runner.setup()
        site = web.TCPSite(runner, host=settings.web_host, port=settings.web_port)
        await site.start()
        log.info("listening on %s:%d", settings.web_host, settings.web_port)
        if settings.webhook_base_url:
            stop = asyncio.Event()
            _install_signal_handlers(stop)
            await stop.wait()
        else:
            await dp.start_polling(bot, close_bot_session=False)
    finally:
        # Also clean up if registration or binding the HTTP port fails.
        await runner.cleanup()
        scheduler.shutdown()
        await bot.session.close()
        from bot.db.base import get_engine

        await get_engine().dispose()


async def _configure_delivery(bot: Bot, settings) -> None:
    """Switch delivery modes while preserving queued updates by default."""
    if settings.webhook_mode:
        await bot.set_webhook(
            url=settings.webhook_url,
            secret_token=settings.webhook_secret,
            drop_pending_updates=settings.drop_pending_updates,
        )
        log.info("webhook registered → %s", settings.masked_webhook_url())
    else:
        await bot.delete_webhook(drop_pending_updates=settings.drop_pending_updates)
        log.info("WEBHOOK_BASE_URL is empty — starting in polling mode.")
