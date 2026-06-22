from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.callbacks.factories import AdminGwCb


def admin_giveaway_panel(giveaway_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=AdminGwCb(action="cancel", giveaway_id=giveaway_id).pack(),
                ),
            ],
        ]
    )
