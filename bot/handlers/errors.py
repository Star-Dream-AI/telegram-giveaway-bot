from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ErrorEvent

log = logging.getLogger("bot.handlers.errors")


def build_errors_router() -> Router:
    router = Router(name="errors")

    @router.errors()
    async def on_error(event: ErrorEvent) -> bool:
        log.exception("unhandled error: %r", event.exception)
        return True

    return router
