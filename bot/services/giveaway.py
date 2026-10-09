from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot

from bot.db.base import get_sessionmaker
from bot.db.models import Giveaway, GiveawayStatus
from bot.db.repo import (
    GiveawayView,
    TierShortfall,
    blacklist_ids,
    claim_giveaway,
    create_giveaway,
    current_winner_ids,
    get_giveaway,
    list_participants,
    log_winners,
    replace_tier_winners,
    set_giveaway_message,
)
from bot.services.notifier import publish_winners
from bot.utils.rng import sample_winners

log = logging.getLogger("bot.services.giveaway")


async def create(
    *,
    creator_id: int,
    title: str,
    description: str,
    prize_tiers: list[dict],
    required_channels: list[str],
    end_at: datetime,
    turnstile_required: bool,
) -> Giveaway:
    sm = get_sessionmaker()
    async with sm() as session:
        gw = await create_giveaway(
            session,
            creator_id=creator_id,
            title=title,
            description=description,
            prize_tiers=prize_tiers,
            required_channels=required_channels,
            end_at=end_at,
            turnstile_required=turnstile_required,
        )
        await session.commit()
        return gw


async def attach_message(giveaway_id: int, chat_id: int, message_id: int) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        await set_giveaway_message(session, giveaway_id, chat_id, message_id)
        await session.commit()


async def cancel(giveaway_id: int) -> bool:
    sm = get_sessionmaker()
    async with sm() as session:
        gw = await claim_giveaway(
            session,
            giveaway_id,
            GiveawayStatus.running,
            new_status=GiveawayStatus.canceled,
        )
        if gw is None:
            return False
        await session.commit()
        return True


def _tier_count(prize_tiers: list[dict] | None, tier_name: str) -> int:
    for tier in prize_tiers or []:
        if str(tier.get("name")) == tier_name:
            return int(tier.get("count", 0) or 0)
    return 0


class RerollError(RuntimeError):
    """Why a reroll was refused.  The handler turns this into user text."""


def _pick_tier(
    participants,
    tier_name: str,
    count: int,
    already: set[int],
    banned: set[int],
) -> tuple[list, TierShortfall | None]:
    """Draw ``count`` winners, honouring both exclusion sets.

    ``already`` = winners from other tiers of this giveaway; ``banned`` =
    accounts blacklisted after they entered.  Both were ignored before, so
    one person could collect every tier and a banned user could still win.

    A tier that cannot be filled in full returns a :class:`TierShortfall`
    instead of quietly handing out fewer prizes (P1-5).
    """
    pool = [p for p in participants if p.user_id not in already and p.user_id not in banned]
    picked = sample_winners(pool, count)
    if len(picked) < count:
        return picked, TierShortfall(tier=tier_name, requested=count, granted=len(picked))
    return picked, None


async def reroll_tier(
    giveaway_id: int, tier_name: str, bot: Bot
) -> tuple[list[tuple[int, str]], TierShortfall | None]:
    """Re-draw one tier of a **finished** giveaway.

    The old implementation happily re-ran on a running giveaway, which
    published winners early and then let the timer draw again - and it drew
    from the full pool with no memory of previous winners, so one user could
    win every tier and the same tier could be "rerolled" into itself.  Now:

    * only a finished giveaway may be rerolled;
    * the previous draw is stamped ``replaced_at`` (audit trail kept);
    * current winners of other tiers are excluded;
    * blacklisted users are excluded.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        gw = await claim_giveaway(session, giveaway_id, GiveawayStatus.finished)
        if gw is None:
            existing = await get_giveaway(session, giveaway_id)
            if existing is None:
                return [], None
            raise RerollError(existing.status.value)

        count = _tier_count(gw.prize_tiers, tier_name)
        if count <= 0:
            raise RerollError("no_such_tier")

        participants = await list_participants(session, giveaway_id, verified_only=True)
        if not participants:
            return [], TierShortfall(tier=tier_name, requested=count, granted=0)

        # Everyone already holding a live win, *including* the tier being
        # rerolled (its rows are about to be superseded).
        winners_elsewhere = await current_winner_ids(session, giveaway_id)
        banned = await blacklist_ids(session, [p.user_id for p in participants])
        picked, shortfall = _pick_tier(participants, tier_name, count, winners_elsewhere, banned)

        await replace_tier_winners(session, giveaway_id, tier_name)
        picks = [(p.user_id, tier_name) for p in picked]
        await log_winners(session, giveaway_id, picks)
        view = GiveawayView.of(gw)
        await session.commit()

    await publish_winners(
        bot,
        view,
        winners=picks,
        reason="reroll",
        shortfalls=[shortfall] if shortfall is not None else [],
    )
    return picks, shortfall


async def finalize_and_publish(giveaway_id: int, bot: Bot) -> None:
    """Draw every tier, mark the giveaway finished, announce the result."""
    sm = get_sessionmaker()
    async with sm() as session:
        gw = await claim_giveaway(
            session,
            giveaway_id,
            GiveawayStatus.running,
            new_status=GiveawayStatus.finished,
        )
        if gw is None:
            log.info("finalize: giveaway %d already closed or missing", giveaway_id)
            return
        participants = await list_participants(session, giveaway_id, verified_only=True)
        banned = await blacklist_ids(session, [p.user_id for p in participants])
        view = GiveawayView.of(gw)

        picks: list[tuple[int, str]] = []
        shortfalls: list[TierShortfall] = []
        for tier in gw.prize_tiers or []:
            name = str(tier.get("name", "")) or "?"
            count = int(tier.get("count", 0) or 0)
            if count <= 0:
                continue
            already = {u for u, _ in picks}
            picked, shortfall = _pick_tier(participants, name, count, already, banned)
            picks.extend((p.user_id, name) for p in picked)
            if shortfall is not None:
                # P1-5: sample_winners() clamps k to the pool size, so a
                # tier used to shrink in silence and the post announced a
                # prize nobody received.  The shortfall is now reported.
                shortfalls.append(shortfall)

        await log_winners(session, giveaway_id, picks)
        await session.commit()

    # ``view`` is a frozen dataclass, not the live ORM row: the old code
    # passed a session-scoped Giveaway across the session boundary and only
    # survived because expire_on_commit=False happened to be set.
    await publish_winners(bot, view, winners=picks, shortfalls=shortfalls)
