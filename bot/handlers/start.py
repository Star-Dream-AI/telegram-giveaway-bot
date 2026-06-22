from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.callbacks.factories import LangCb
from bot.db.base import get_sessionmaker
from bot.db.repo import (
    ensure_user,
    get_user_lang,
    me_participations,
    set_user_lang,
)
from bot.i18n import available_langs, t
from bot.keyboards.lang import lang_keyboard
from bot.settings import get_settings

log = logging.getLogger("bot.handlers.start")


async def _lang_of(user_id: int) -> str:
    sm = get_sessionmaker()
    async with sm() as session:
        lang = await get_user_lang(session, user_id)
    return lang or get_settings().default_lang


def build_start_router() -> Router:
    router = Router(name="start")

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            await ensure_user(
                session, message.from_user.id, message.from_user.username
            )
            await session.commit()
        lang = await _lang_of(message.from_user.id)
        await message.answer(t("start.welcome", lang))

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        lang = await _lang_of(message.from_user.id)
        await message.answer(t("start.help", lang))

    @router.message(Command("lang"))
    async def on_lang(message: Message) -> None:
        lang = await _lang_of(message.from_user.id)
        await message.answer(
            t("start.lang_prompt", lang),
            reply_markup=lang_keyboard(available_langs()),
        )

    @router.callback_query(LangCb.filter())
    async def on_lang_pick(cq: CallbackQuery, callback_data: LangCb) -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            await set_user_lang(session, cq.from_user.id, callback_data.code)
            await session.commit()
        await cq.message.edit_text(t("start.lang_set", callback_data.code))
        await cq.answer()

    @router.message(Command("me"))
    async def on_me(message: Message) -> None:
        lang = await _lang_of(message.from_user.id)
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await me_participations(session, message.from_user.id, limit=20)
        if not rows:
            await message.answer(t("start.me_empty", lang))
            return
        lines = [t("start.me_header", lang, count=len(rows))]
        for _participant, gw in rows:
            lines.append(
                t(
                    "start.me_row",
                    lang,
                    id=gw.id,
                    title=gw.title,
                    status=gw.status.value if hasattr(gw.status, "value") else gw.status,
                )
            )
        await message.answer("\n".join(lines))

    return router
