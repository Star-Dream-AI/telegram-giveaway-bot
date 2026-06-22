from __future__ import annotations

from aiogram import Router

from bot.handlers.admin_create import build_admin_create_router
from bot.handlers.admin_manage import build_admin_manage_router
from bot.handlers.errors import build_errors_router
from bot.handlers.participate import build_participate_router
from bot.handlers.start import build_start_router


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(build_start_router())
    root.include_router(build_participate_router())
    root.include_router(build_admin_create_router())
    root.include_router(build_admin_manage_router())
    root.include_router(build_errors_router())
    return root
