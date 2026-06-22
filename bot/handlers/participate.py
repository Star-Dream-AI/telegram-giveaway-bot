from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callbacks.factories import JoinCb
from bot.db.base import get_sessionmaker
from bot.db.models import GiveawayStatus
from bot.db.repo import (
    add_participant,
    ensure_user,
    get_giveaway,
    has_participated,
    is_blacklisted,
)
from bot.handlers.common import lang_of
from bot.i18n import t

log = logging.getLogger("bot.handlers.participate")


def build_participate_router() -> Router:
    router = Router(name="participate")

    @router.callback_query(JoinCb.filter())
    async def on_join(cq: CallbackQuery, callback_data: JoinCb) -> None:
        uid = cq.from_user.id
        username = cq.from_user.username
        lang = await lang_of(uid)
        gid = callback_data.giveaway_id

        sm = get_sessionmaker()
        async with sm() as session:
            await ensure_user(session, uid, username)
            if await is_blacklisted(session, uid):
                await session.commit()
                await cq.answer(t("join.banned", lang), show_alert=True)
                return
            gw = await get_giveaway(session, gid)
            if gw is None or gw.status != GiveawayStatus.running:
                await session.commit()
                await cq.answer(t("join.ended", lang), show_alert=True)
                return
            if gw.end_at <= datetime.now(timezone.utc):
                await session.commit()
                await cq.answer(t("join.ended", lang), show_alert=True)
                return
            if await has_participated(session, gid, uid):
                await session.commit()
                await cq.answer(t("join.already", lang), show_alert=True)
                return
            await add_participant(
                session,
                giveaway_id=gid,
                user_id=uid,
                username=username,
                verified=True,
            )
            await session.commit()

        await cq.answer(t("join.ok", lang), show_alert=False)

    return router
