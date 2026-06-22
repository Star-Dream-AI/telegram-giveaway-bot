from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.callbacks.factories import JoinCb
from bot.i18n import t


def participate_keyboard(giveaway_id: int, lang: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("button.join", lang),
                    callback_data=JoinCb(giveaway_id=giveaway_id).pack(),
                )
            ]
        ]
    )
