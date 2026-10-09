from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ErrorEvent

log = logging.getLogger("bot.handlers.errors")


def build_errors_router() -> Router:
    router = Router(name="errors")

    @router.errors()
    async def on_error(event: ErrorEvent) -> bool:
        # log.exception() must be called *inside* an active exception
        # context, otherwise it logs "NoneType: None" and hides the real
        # traceback - which is the opposite of what an error handler is for.
        log.error(
            "unhandled error in update %s: %r",
            getattr(event.update, "update_id", "?"),
            event.exception,
            exc_info=event.exception,
        )
        return True

    return router
