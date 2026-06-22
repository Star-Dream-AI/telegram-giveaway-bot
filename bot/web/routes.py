from __future__ import annotations

import logging

from aiohttp import web

log = logging.getLogger("bot.web.routes")


async def healthz(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


def register_web_routes(app: web.Application) -> None:
    app.router.add_get("/healthz", healthz)
    log.info("web routes registered: /healthz")
