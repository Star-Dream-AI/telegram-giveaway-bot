from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.callbacks.factories import LangCb
from bot.db.base import get_sessionmaker
from bot.db.models import GiveawayStatus
from bot.db.repo import ensure_user, get_giveaway, me_participations, set_user_lang
from bot.db.turnstile_repo import has_valid_proof
from bot.handlers.common import lang_of
from bot.i18n import available_langs, t
from bot.keyboards.lang import lang_keyboard
from bot.keyboards.participate import participate_keyboard

log = logging.getLogger("bot.handlers.start")


def build_start_router() -> Router:
    router = Router(name="start")

    @router.message(CommandStart())
    async def on_start(message: Message, command: CommandObject) -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            await ensure_user(session, message.from_user.id, message.from_user.username)
            await session.commit()
        lang = await lang_of(message.from_user.id)
        if command.args and command.args.startswith("verify_"):
            parts = command.args.split("_", 2)
            if len(parts) != 3 or not parts[1].isdigit():
                await message.answer(t("join.verify_expired", lang))
                return
            gid, nonce = int(parts[1]), parts[2]
            if not 0 < gid <= 2**63 - 1 or len(nonce) != 32:
                await message.answer(t("join.verify_expired", lang))
                return
            async with sm() as session:
                gw = await get_giveaway(session, gid)
                valid = (
                    gw is not None
                    and gw.status == GiveawayStatus.running
                    and gw.end_at > datetime.now(timezone.utc)
                    and await has_valid_proof(session, message.from_user.id, gid, nonce=nonce)
                )
            if not valid:
                await message.answer(t("join.verify_expired", lang))
                return
            await message.answer(
                t("join.verify_join_prompt", lang),
                reply_markup=participate_keyboard(gid, lang),
            )
            return
        await message.answer(t("start.welcome", lang))

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        lang = await lang_of(message.from_user.id)
        await message.answer(t("start.help", lang))

    @router.message(Command("lang"))
    async def on_lang(message: Message) -> None:
        lang = await lang_of(message.from_user.id)
        await message.answer(
            t("start.lang_prompt", lang),
            reply_markup=lang_keyboard(available_langs()),
        )

    @router.callback_query(LangCb.filter())
    async def on_lang_pick(cq: CallbackQuery, callback_data: LangCb) -> None:
        if callback_data.code not in available_langs():
            await cq.answer()
            return
        current = await lang_of(cq.from_user.id)
        if current == callback_data.code:
            await cq.answer(
                t("start.lang_unchanged", callback_data.code, language=callback_data.code)
            )
            return
        sm = get_sessionmaker()
        async with sm() as session:
            await set_user_lang(session, cq.from_user.id, callback_data.code)
            await session.commit()
        await cq.message.edit_text(
            t("start.lang_set", callback_data.code, language=callback_data.code)
        )
        await cq.answer()

    @router.message(Command("me"))
    async def on_me(message: Message) -> None:
        lang = await lang_of(message.from_user.id)
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
