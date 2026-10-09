from __future__ import annotations

import logging
from typing import Sequence

from aiogram import Bot

from bot.db.base import get_sessionmaker
from bot.db.repo import (
    GiveawayView,
    TierShortfall,
    get_giveaway_view,
    list_winners,
    mark_winner_notified,
)
from bot.i18n import t
from bot.settings import get_settings
from bot.utils.esc import esc, user_mention

log = logging.getLogger("bot.services.notifier")

NL = "\n"


async def _winner_langs(session, winner_ids: Sequence[int]) -> dict[int, str]:
    if not winner_ids:
        return {}
    from sqlalchemy import select

    from bot.db.models import User

    stmt = select(User).where(User.user_id.in_(list(winner_ids)))
    return {u.user_id: u.lang for u in (await session.execute(stmt)).scalars().all() if u.lang}


async def publish_winners(
    bot: Bot,
    giveaway: GiveawayView,
    winners: Sequence[tuple[int, str]],
    *,
    shortfalls: Sequence[TierShortfall] = (),
    reason: str = "draw",
) -> None:
    """Announce a draw under the original post and DM every winner.

    ``giveaway`` is a frozen :class:`GiveawayView`, not a live ORM row —
    the caller has already left its session, and relying on
    ``expire_on_commit=False`` to keep a detached instance readable was an
    invisible contract that breaks the moment someone aligns the
    sessionmaker with the SQLAlchemy default.

    Every DM outcome is now persisted (``notified_ok`` / ``notify_error`` /
    ``notified_at``) via ``mark_winner_notified``; previously the result was
    logged and forgotten, so a failed delivery left no trace and no way to
    retry selectively.
    """
    default_lang = get_settings().default_lang
    winner_langs: dict[int, str] = {}
    creator_username: str | None = None

    sm = get_sessionmaker()
    async with sm() as session:
        from bot.db.models import User as DBUser

        creator = await session.get(DBUser, giveaway.creator_id)
        if creator is not None:
            creator_username = creator.username
        winner_langs = await _winner_langs(session, [w[0] for w in winners])

    # 1. public announcement, replied under the giveaway post
    lines = []
    if reason != "draw":
        lines.append(t("giveaway.reroll_header", default_lang, id=giveaway.id))
    lines.append(t("giveaway.finished_header", default_lang, id=giveaway.id))
    if not winners:
        lines.append(
            t(
                "giveaway.no_participants" if reason == "draw" else "giveaway.no_winners",
                default_lang,
            )
        )
    else:
        lines.append(t("giveaway.winners_header", default_lang))
        for user_id, tier in winners:
            lines.append(
                t("giveaway.winner_line", default_lang, tier=tier, mention=user_mention(user_id))
            )
    for short in shortfalls:
        lines.append(
            t(
                "giveaway.tier_short",
                default_lang,
                tier=short.tier,
                requested=short.requested,
                granted=short.granted,
                missing=short.missing,
            )
        )
    text = NL.join(lines)

    if giveaway.chat_id and giveaway.message_id:
        try:
            await bot.send_message(
                giveaway.chat_id,
                text,
                reply_to_message_id=giveaway.message_id,
            )
        except Exception:
            log.exception("publish_winners: send_message failed")

    # 2. private winner notifications, each with a persisted outcome
    for user_id, tier in winners:
        winner_lang = winner_langs.get(user_id) or default_lang
        if creator_username:
            creator_str = esc(f"@{creator_username}")
        else:
            creator_str = user_mention(
                giveaway.creator_id,
                t("winner_dm.host", winner_lang, id=giveaway.creator_id),
            )
        body = t(
            "winner_dm.body",
            winner_lang,
            title=giveaway.title,
            tier=tier,
            creator=creator_str,
        )
        error: str | None = None
        ok = True
        try:
            await bot.send_message(user_id, body)
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"
            log.warning("winner DM failed for %d: %s", user_id, error)
        await _record_delivery(giveaway.id, user_id, tier, ok, error)


async def _record_delivery(
    giveaway_id: int, user_id: int, tier: str, ok: bool, error: str | None
) -> None:
    """Write notified_at / notified_ok / notify_error for one winner row."""
    sm = get_sessionmaker()
    async with sm() as session:
        rows = [
            w
            for w in await list_winners(session, giveaway_id)
            if w.user_id == user_id and w.tier_name == tier
        ]
        for row in rows:
            await mark_winner_notified(session, row.id, ok=ok, error=error)
        await session.commit()


async def retry_pending(bot: Bot, giveaway_id: int) -> int:
    """Re-send DMs for winners whose delivery was never confirmed.

    Includes both unattempted and failed current winners. Returns the
    number of messages actually sent; superseded winners are never retried.
    """
    from bot.db.repo import pending_notifications

    sm = get_sessionmaker()
    async with sm() as session:
        rows = await pending_notifications(session, giveaway_id)
        giveaway = await get_giveaway_view(session, giveaway_id)
        langs = await _winner_langs(session, [r.user_id for r in rows])
        jobs = [(r.id, r.user_id, r.tier_name) for r in rows]
    if giveaway is None or not jobs:
        return 0

    default_lang = get_settings().default_lang
    sm = get_sessionmaker()
    async with sm() as session:
        from bot.db.models import User as DBUser

        creator = await session.get(DBUser, giveaway.creator_id)
        creator_username = creator.username if creator else None

    sent = 0
    for row_id, user_id, tier in jobs:
        winner_lang = langs.get(user_id) or default_lang
        creator_str = (
            esc(f"@{creator_username}")
            if creator_username
            else user_mention(
                giveaway.creator_id, t("winner_dm.host", winner_lang, id=giveaway.creator_id)
            )
        )
        body = t(
            "winner_dm.body", winner_lang, title=giveaway.title, tier=tier, creator=creator_str
        )
        ok, error = True, None
        try:
            await bot.send_message(user_id, body)
            sent += 1
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"
        sm = get_sessionmaker()
        async with sm() as session:
            await mark_winner_notified(session, row_id, ok=ok, error=error)
            await session.commit()
    log.info("retry_pending: re-sent %d/%d winner DMs", sent, len(jobs))
    return sent
