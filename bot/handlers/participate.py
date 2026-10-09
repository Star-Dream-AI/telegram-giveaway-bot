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
    claim_giveaway,
    ensure_user,
    get_giveaway,
    has_participated,
    is_blacklisted,
)
from bot.db.turnstile_repo import consume_proof, has_valid_proof
from bot.handlers.common import lang_of
from bot.i18n import t
from bot.services.channels import missing_channels
from bot.utils.esc import esc_list

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
            await session.commit()

            if await is_blacklisted(session, uid):
                await cq.answer(t("join.banned", lang), show_alert=True)
                return

            if await has_participated(session, gid, uid):
                await cq.answer(t("join.already", lang), show_alert=True)
                return

            gw = await get_giveaway(session, gid)
            if gw is None or gw.status != GiveawayStatus.running:
                await cq.answer(t("join.ended", lang), show_alert=True)
                return

            # end_at comes back through UTCDateTime, so this is always
            # aware-vs-aware.  With the old DateTime(timezone=True) SQLite
            # handed back a naive datetime and this line raised TypeError
            # on every single join - the button did nothing, ever.
            end_at = gw.end_at
            if end_at is not None and end_at <= datetime.now(timezone.utc):
                await cq.answer(t("join.ended", lang), show_alert=True)
                return

            # Required channels: now actually enforced, and fail-closed.
            if gw.required_channels:
                missing = await missing_channels(cq.bot, uid, gw.required_channels)
                if missing:
                    await cq.answer(
                        t("join.not_member", lang, channels=esc_list(missing)),
                        show_alert=True,
                    )
                    return

            # Channel checks await Telegram. Close the read transaction and
            # recheck under a database write lock before accepting the entry;
            # a draw/cancel/ban may have completed while we were waiting.
            await session.rollback()
            gw = await claim_giveaway(session, gid, GiveawayStatus.running, require_open=True)
            if gw is None:
                await cq.answer(t("join.ended", lang), show_alert=True)
                return
            if await is_blacklisted(session, uid):
                await cq.answer(t("join.banned", lang), show_alert=True)
                return
            if await has_participated(session, gid, uid):
                await cq.answer(t("join.already", lang), show_alert=True)
                return

            # Turnstile: a live single-use proof is required to enter.  This
            # used to be add_participant(..., verified=True) hard-coded, i.e.
            # the captcha was a screenshot of a shield emoji.
            needs_proof = False
            if gw.turnstile_required:
                if not await has_valid_proof(session, uid, gid):
                    # Callback alerts cannot open a URL and are limited to
                    # 200 characters. Send a clickable button in private chat.
                    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

                    from bot.settings import get_settings

                    await session.rollback()
                    try:
                        await cq.bot.send_message(
                            uid,
                            t("join.need_verify_prompt", lang),
                            reply_markup=InlineKeyboardMarkup(
                                inline_keyboard=[
                                    [
                                        InlineKeyboardButton(
                                            text=t("button.verify", lang),
                                            url=get_settings().verify_url(gid, uid),
                                        )
                                    ]
                                ]
                            ),
                        )
                    except Exception:
                        await cq.answer(t("join.open_bot_first", lang), show_alert=True)
                        return
                    await cq.answer(t("join.verify_sent", lang), show_alert=True)
                    return
                needs_proof = True

            # A giveaway that does not require the captcha admits an
            # unchallenged entry.  Marking it unverified instead would drop it
            # from the draw pool entirely, because the draw only ever reads
            # verified participants.
            verified = not gw.turnstile_required
            if needs_proof:
                # One challenge, one entry.  Spending the proof here (rather
                # than at verification time) means re-sharing a solved link
                # cannot mint a second entry for another giveaway.
                spent = await consume_proof(session, user_id=uid, giveaway_id=gid)
                if not spent:
                    await session.rollback()
                    await cq.answer(t("join.verify_expired", lang), show_alert=True)
                    return
                verified = True

            # add_participant() returns None when the row already exists
            # (SAVEPOINT, not a session-wide rollback).  Ignoring that return
            # is what made a double-tap silently drop the entry and still
            # confirm success.
            created = await add_participant(
                session,
                giveaway_id=gid,
                user_id=uid,
                username=username,
                verified=verified,
            )
            if created is None:
                await session.rollback()
                await cq.answer(t("join.already", lang), show_alert=True)
                return
            await session.commit()

        await cq.answer(t("join.ok", lang), show_alert=False)

    return router
