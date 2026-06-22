from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.callbacks.factories import LangCb

_LABELS = {"zh": "简体中文 🇨🇳", "en": "English 🇬🇧"}


def lang_keyboard(langs: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for code in langs:
        label = _LABELS.get(code, code)
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=LangCb(code=code).pack(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
