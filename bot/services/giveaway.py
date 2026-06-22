from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot

from bot.db.base import get_sessionmaker
from bot.db.models import Giveaway, GiveawayStatus
from bot.db.repo import (
    create_giveaway,
    get_giveaway,
    list_participants,
    log_winners,
    set_giveaway_message,
    update_giveaway_status,
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
        gw = await get_giveaway(session, giveaway_id)
        if gw is None or gw.status != GiveawayStatus.running:
            return False
        await update_giveaway_status(session, giveaway_id, GiveawayStatus.canceled)
        await session.commit()
        return True


async def reroll_tier(
    giveaway_id: int, tier_name: str, bot: Bot
) -> list[tuple[int, str]]:
    sm = get_sessionmaker()
    async with sm() as session:
        gw = await get_giveaway(session, giveaway_id)
        if gw is None:
            return []
        tier_count = 0
        for t in gw.prize_tiers or []:
            if str(t.get("name")) == tier_name:
                tier_count = int(t.get("count", 0))
                break
        if tier_count <= 0:
            return []
        participants = await list_participants(session, giveaway_id, verified_only=True)
        if not participants:
            return []
        picked = sample_winners(participants, tier_count)
        picks = [(p.user_id, tier_name) for p in picked]
        await log_winners(session, giveaway_id, picks)
        await session.commit()
    gw_for_publish = await _reload(giveaway_id)
    if gw_for_publish is not None:
        await publish_winners(bot, gw_for_publish, winners=picks)
    return picks


async def _reload(giveaway_id: int) -> Giveaway | None:
    sm = get_sessionmaker()
    async with sm() as session:
        return await get_giveaway(session, giveaway_id)


async def finalize_and_publish(giveaway_id: int, bot: Bot) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        gw = await get_giveaway(session, giveaway_id)
        if gw is None:
            log.warning("finalize: giveaway %d not found", giveaway_id)
            return
        if gw.status != GiveawayStatus.running:
            log.info(
                "finalize: giveaway %d not running (status=%s)",
                giveaway_id, gw.status,
            )
            return
        participants = await list_participants(session, giveaway_id, verified_only=True)
        if not participants:
            await update_giveaway_status(session, giveaway_id, GiveawayStatus.finished)
            await session.commit()
            await publish_winners(bot, gw, winners=[])
            return

        picks: list[tuple[int, str]] = []
        for tier in gw.prize_tiers or []:
            name = str(tier.get("name", "")) or "?"
            count = int(tier.get("count", 0))
            if count <= 0:
                continue
            already = {u for u, _ in picks}
            remaining = [p for p in participants if p.user_id not in already]
            picked = sample_winners(remaining, count)
            for p in picked:
                picks.append((p.user_id, name))

        await log_winners(session, giveaway_id, picks)
        await update_giveaway_status(session, giveaway_id, GiveawayStatus.finished)
        await session.commit()
    await publish_winners(bot, gw, winners=picks)
