from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.callbacks.factories import AdminGwCb
from bot.i18n import t


def admin_giveaway_panel(giveaway_id: int, lang: str | None = None) -> InlineKeyboardMarkup:
    """Admin-only controls for one giveaway.

    The public post must stay a single join button, so the panel is sent
    in the admin chat by /newgiveaway instead of next to the post.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u274c " + t("admin.btn_cancel", lang),
                    callback_data=AdminGwCb(
                        action="cancel", giveaway_id=giveaway_id
                    ).pack(),
                ),
            ],
        ]
    )