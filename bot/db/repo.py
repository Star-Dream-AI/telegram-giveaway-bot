from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    AdminUser,
    Blacklist,
    Giveaway,
    GiveawayStatus,
    Participant,
    User,
    WinnerLog,
)


async def ensure_user(
    session: AsyncSession,
    user_id: int,
    username: str | None = None,
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(user_id=user_id, username=username)
        session.add(user)
        await session.flush()
        return user
    if username and user.username != username:
        user.username = username
    return user


async def set_user_lang(session: AsyncSession, user_id: int, lang: str) -> None:
    user = await ensure_user(session, user_id)
    user.lang = lang


async def get_user_lang(session: AsyncSession, user_id: int) -> str | None:
    user = await session.get(User, user_id)
    return user.lang if user else None


async def create_giveaway(
    session: AsyncSession,
    *,
    creator_id: int,
    title: str,
    description: str,
    prize_tiers: list[dict],
    required_channels: list[str],
    end_at: datetime,
    turnstile_required: bool,
) -> Giveaway:
    gw = Giveaway(
        creator_id=creator_id,
        title=title,
        description=description,
        prize_tiers=prize_tiers,
        required_channels=required_channels,
        end_at=end_at,
        turnstile_required=turnstile_required,
    )
    session.add(gw)
    await session.flush()
    return gw


async def get_giveaway(session: AsyncSession, giveaway_id: int) -> Giveaway | None:
    return await session.get(Giveaway, giveaway_id)


async def list_running_giveaways(session: AsyncSession) -> Sequence[Giveaway]:
    stmt = select(Giveaway).where(Giveaway.status == GiveawayStatus.running)
    return (await session.execute(stmt)).scalars().all()


async def list_giveaways_by_creator(
    session: AsyncSession,
    creator_id: int,
    status: GiveawayStatus | None = None,
) -> Sequence[Giveaway]:
    stmt = select(Giveaway).where(Giveaway.creator_id == creator_id)
    if status is not None:
        stmt = stmt.where(Giveaway.status == status)
    stmt = stmt.order_by(Giveaway.created_at.desc())
    return (await session.execute(stmt)).scalars().all()


async def update_giveaway_status(
    session: AsyncSession, giveaway_id: int, status: GiveawayStatus
) -> None:
    gw = await session.get(Giveaway, giveaway_id)
    if gw is not None:
        gw.status = status


async def set_giveaway_message(
    session: AsyncSession, giveaway_id: int, chat_id: int, message_id: int
) -> None:
    gw = await session.get(Giveaway, giveaway_id)
    if gw is not None:
        gw.chat_id = chat_id
        gw.message_id = message_id


async def add_participant(
    session: AsyncSession,
    *,
    giveaway_id: int,
    user_id: int,
    username: str | None,
    verified: bool = True,
    turnstile_token: str | None = None,
) -> Participant | None:
    p = Participant(
        giveaway_id=giveaway_id,
        user_id=user_id,
        username=username,
        verified=verified,
        turnstile_token=turnstile_token,
    )
    session.add(p)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return None
    return p


async def has_participated(
    session: AsyncSession, giveaway_id: int, user_id: int
) -> bool:
    stmt = select(Participant.id).where(
        Participant.giveaway_id == giveaway_id,
        Participant.user_id == user_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def count_participants(
    session: AsyncSession, giveaway_id: int, verified_only: bool = True
) -> int:
    stmt = select(func.count(Participant.id)).where(
        Participant.giveaway_id == giveaway_id
    )
    if verified_only:
        stmt = stmt.where(Participant.verified.is_(True))
    return int((await session.execute(stmt)).scalar_one())


async def list_participants(
    session: AsyncSession, giveaway_id: int, verified_only: bool = True
) -> Sequence[Participant]:
    stmt = select(Participant).where(Participant.giveaway_id == giveaway_id)
    if verified_only:
        stmt = stmt.where(Participant.verified.is_(True))
    return (await session.execute(stmt)).scalars().all()


async def log_winners(
    session: AsyncSession,
    giveaway_id: int,
    winners: list[tuple[int, str]],
) -> list[WinnerLog]:
    rows = [
        WinnerLog(giveaway_id=giveaway_id, user_id=uid, tier_name=tier)
        for uid, tier in winners
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def list_winners(
    session: AsyncSession, giveaway_id: int
) -> Sequence[WinnerLog]:
    stmt = select(WinnerLog).where(WinnerLog.giveaway_id == giveaway_id)
    return (await session.execute(stmt)).scalars().all()


async def mark_winner_notified(session: AsyncSession, winner_id: int) -> None:
    w = await session.get(WinnerLog, winner_id)
    if w is not None:
        w.notified_at = datetime.now(timezone.utc)


async def is_admin_db(session: AsyncSession, user_id: int) -> bool:
    return (await session.get(AdminUser, user_id)) is not None


async def add_admin(
    session: AsyncSession, user_id: int, added_by: int | None = None
) -> None:
    if await session.get(AdminUser, user_id):
        return
    session.add(AdminUser(user_id=user_id, added_by=added_by))


async def remove_admin(session: AsyncSession, user_id: int) -> None:
    a = await session.get(AdminUser, user_id)
    if a is not None:
        await session.delete(a)


async def list_admins(session: AsyncSession) -> Sequence[AdminUser]:
    return (await session.execute(select(AdminUser))).scalars().all()


async def is_blacklisted(session: AsyncSession, user_id: int) -> bool:
    return (await session.get(Blacklist, user_id)) is not None


async def ban_user(
    session: AsyncSession,
    user_id: int,
    reason: str | None = None,
    banned_by: int | None = None,
) -> None:
    existing = await session.get(Blacklist, user_id)
    if existing is not None:
        existing.reason = reason
        existing.banned_by = banned_by
        return
    session.add(Blacklist(user_id=user_id, reason=reason, banned_by=banned_by))


async def unban_user(session: AsyncSession, user_id: int) -> None:
    b = await session.get(Blacklist, user_id)
    if b is not None:
        await session.delete(b)


async def me_participations(
    session: AsyncSession, user_id: int, limit: int = 20
) -> list[tuple[Participant, Giveaway]]:
    stmt = (
        select(Participant, Giveaway)
        .join(Giveaway, Participant.giveaway_id == Giveaway.id)
        .where(Participant.user_id == user_id)
        .order_by(Participant.joined_at.desc())
        .limit(limit)
    )
    return [(p, g) for p, g in (await session.execute(stmt)).all()]
