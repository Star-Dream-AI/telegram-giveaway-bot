from __future__ import annotations

import logging

from aiohttp import web

from bot.web.verify import register_verify_routes

log = logging.getLogger("bot.web.routes")


async def healthz(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


def register_web_routes(app: web.Application) -> None:
    app.router.add_get("/healthz", healthz)
    register_verify_routes(app)
    log.info("web routes registered: /healthz, /verify/{gid}, /done/{gid}")
