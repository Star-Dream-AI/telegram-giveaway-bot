from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class LangCb(CallbackData, prefix="lang"):
    code: str


class JoinCb(CallbackData, prefix="join"):
    giveaway_id: int


class AdminGwCb(CallbackData, prefix="agw"):
    action: str
    giveaway_id: int
