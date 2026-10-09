from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import func, or_, select, update
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class GiveawayView:
    """An immutable snapshot of the fields ``publish_winners()`` needs.

    The previous implementation passed a live ``Giveaway`` ORM instance
    across a session boundary and relied on ``expire_on_commit=False`` in
    ``bot/db/base.py`` to keep it usable.  That is an invisible contract:
    anyone "aligning" the sessionmaker with the SQLAlchemy default turns a
    working draw into ``DetachedInstanceError`` at the exact moment winners
    are being announced.  A plain frozen dataclass removes the dependency.
    """

    id: int
    creator_id: int
    title: str
    status: GiveawayStatus
    chat_id: int | None
    message_id: int | None
    turnstile_required: bool
    required_channels: tuple[str, ...] = ()
    prize_tiers: tuple[dict[str, Any], ...] = ()
    end_at: datetime | None = None

    @classmethod
    def of(cls, gw: Giveaway) -> "GiveawayView":
        return cls(
            id=gw.id,
            creator_id=gw.creator_id,
            title=gw.title,
            status=gw.status,
            chat_id=gw.chat_id,
            message_id=gw.message_id,
            turnstile_required=gw.turnstile_required,
            required_channels=tuple(gw.required_channels or ()),
            prize_tiers=tuple(dict(t) for t in (gw.prize_tiers or ())),
            end_at=gw.end_at,
        )


@dataclass(frozen=True, slots=True)
class TierShortfall:
    """A prize tier that could not be fully awarded."""

    tier: str
    requested: int
    granted: int

    @property
    def missing(self) -> int:
        return max(0, self.requested - self.granted)


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------


async def ensure_user(
    session: AsyncSession,
    user_id: int,
    username: str | None = None,
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(user_id=user_id, username=username)
        try:
            async with session.begin_nested():
                session.add(user)
                await session.flush()
        except IntegrityError:
            user = await session.get(User, user_id)
            if user is None:
                raise
    if username and user.username != username:
        user.username = username
    return user


async def set_user_lang(session: AsyncSession, user_id: int, lang: str) -> None:
    user = await ensure_user(session, user_id)
    user.lang = lang


async def get_user_lang(session: AsyncSession, user_id: int) -> str | None:
    user = await session.get(User, user_id)
    return user.lang if user else None


async def get_user_names(session: AsyncSession, user_ids: Sequence[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    stmt = select(User).where(User.user_id.in_(list(user_ids)))
    return {
        u.user_id: u.username for u in (await session.execute(stmt)).scalars().all() if u.username
    }


# --------------------------------------------------------------------------
# giveaways
# --------------------------------------------------------------------------


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


async def get_giveaway_view(session: AsyncSession, giveaway_id: int) -> GiveawayView | None:
    gw = await session.get(Giveaway, giveaway_id)
    return GiveawayView.of(gw) if gw else None


async def list_running_giveaways(session: AsyncSession) -> Sequence[Giveaway]:
    stmt = select(Giveaway).where(Giveaway.status == GiveawayStatus.running)
    return (await session.execute(stmt)).scalars().all()


async def list_giveaways_by_creator(
    session: AsyncSession,
    creator_id: int | None = None,
    status: GiveawayStatus | None = None,
) -> Sequence[Giveaway]:
    """Running giveaways, newest first.

    ``creator_id=None`` means "all admins see all running giveaways".  The
    old handler always filtered by the caller, which contradicted the README
    ("list all running giveaways") while /cancel and /reroll happily acted on
    anybody's giveaway — an inconsistent authorisation model.
    """
    stmt = select(Giveaway)
    if creator_id is not None:
        stmt = stmt.where(Giveaway.creator_id == creator_id)
    if status is not None:
        stmt = stmt.where(Giveaway.status == status)
    stmt = stmt.order_by(Giveaway.created_at.desc(), Giveaway.id.desc())
    return (await session.execute(stmt)).scalars().all()


async def update_giveaway_status(
    session: AsyncSession, giveaway_id: int, status: GiveawayStatus
) -> None:
    gw = await session.get(Giveaway, giveaway_id)
    if gw is not None:
        gw.status = status


async def claim_giveaway(
    session: AsyncSession,
    giveaway_id: int,
    expected_status: GiveawayStatus,
    *,
    new_status: GiveawayStatus | None = None,
    require_open: bool = False,
) -> Giveaway | None:
    """Serialize changes to a giveaway using a conditional database write.

    Unlike SELECT FOR UPDATE, this also acquires a write lock on SQLite.
    The lock lasts until commit/rollback. A status transition and its winner
    rows therefore succeed together, even when multiple workers draw at once.
    """
    stmt = update(Giveaway).where(Giveaway.id == giveaway_id, Giveaway.status == expected_status)
    if require_open:
        stmt = stmt.where(Giveaway.end_at > _now())
    stmt = stmt.values(
        status=new_status if new_status is not None else Giveaway.status
    ).execution_options(synchronize_session=False)
    result = await session.execute(stmt)
    if result.rowcount != 1:
        return None
    return await session.get(Giveaway, giveaway_id, populate_existing=True)


async def set_giveaway_message(
    session: AsyncSession, giveaway_id: int, chat_id: int, message_id: int
) -> None:
    gw = await session.get(Giveaway, giveaway_id)
    if gw is not None:
        gw.chat_id = chat_id
        gw.message_id = message_id


# --------------------------------------------------------------------------
# participants
# --------------------------------------------------------------------------


async def add_participant(
    session: AsyncSession,
    *,
    giveaway_id: int,
    user_id: int,
    username: str | None,
    verified: bool = True,
    turnstile_token_hash: str | None = None,
    turnstile_nonce: str | None = None,
    turnstile_expires_at: datetime | None = None,
) -> Participant | None:
    """Insert a participation row, or return None if it already exists.

    The duplicate case used to ``await session.rollback()``, which throws
    away *every* pending change in the session — and the caller ignored the
    return value, committed, and told the user "you're in!".  Net effect: a
    double-tap on the join button silently discarded the entry while reporting
    success.  A SAVEPOINT confines the failure to this statement, and the
    caller now has to act on the None return.
    """
    p = Participant(
        giveaway_id=giveaway_id,
        user_id=user_id,
        username=username,
        verified=verified,
        turnstile_token=turnstile_token_hash,
        turnstile_nonce=turnstile_nonce,
        turnstile_expires_at=turnstile_expires_at,
        verified_at=_now() if verified else None,
    )
    try:
        async with session.begin_nested():
            session.add(p)
            await session.flush()
    except IntegrityError:
        # The SAVEPOINT confines the failure to this one statement, so
        # everything else the caller staged in this session survives.  A
        # bare `except` that did not re-raise-as-None would blow up the
        # handler on a double tap and answer nothing at all.
        return None
    return p


async def has_participated(session: AsyncSession, giveaway_id: int, user_id: int) -> bool:
    stmt = select(Participant.id).where(
        Participant.giveaway_id == giveaway_id,
        Participant.user_id == user_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def count_participants(
    session: AsyncSession, giveaway_id: int, verified_only: bool = True
) -> int:
    stmt = select(func.count(Participant.id)).where(Participant.giveaway_id == giveaway_id)
    if verified_only:
        stmt = stmt.where(Participant.verified.is_(True))
    return int((await session.execute(stmt)).scalar_one())


async def list_participants(
    session: AsyncSession, giveaway_id: int, verified_only: bool = True
) -> Sequence[Participant]:
    stmt = select(Participant).where(Participant.giveaway_id == giveaway_id)
    if verified_only:
        stmt = stmt.where(Participant.verified.is_(True))
    stmt = stmt.order_by(Participant.id)
    return (await session.execute(stmt)).scalars().all()


async def delete_participations(session: AsyncSession, user_id: int) -> int:
    """Drop every entry of a user across all giveaways.

    Called when an account is banned: without it a blacklisted user keeps a
    seat in the pool and can still be drawn, because the ban was only ever
    checked at join time.
    """
    result = await session.execute(
        Participant.__table__.delete().where(Participant.user_id == user_id)
    )
    return int(result.rowcount or 0)


async def blacklist_ids(session: AsyncSession, ids: Sequence[int]) -> set[int]:
    if not ids:
        return set()
    stmt = select(Blacklist.user_id).where(Blacklist.user_id.in_(list(ids)))
    return set((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------
# winners
# --------------------------------------------------------------------------


async def log_winners(
    session: AsyncSession,
    giveaway_id: int,
    winners: list[tuple[int, str]],
) -> list[WinnerLog]:
    rows = [
        WinnerLog(giveaway_id=giveaway_id, user_id=uid, tier_name=tier) for uid, tier in winners
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def list_winners(
    session: AsyncSession, giveaway_id: int, include_replaced: bool = False
) -> Sequence[WinnerLog]:
    stmt = select(WinnerLog).where(WinnerLog.giveaway_id == giveaway_id)
    if not include_replaced:
        stmt = stmt.where(WinnerLog.replaced_at.is_(None))
    stmt = stmt.order_by(WinnerLog.id)
    return (await session.execute(stmt)).scalars().all()


async def current_winner_ids(session: AsyncSession, giveaway_id: int) -> set[int]:
    stmt = select(WinnerLog.user_id).where(
        WinnerLog.giveaway_id == giveaway_id, WinnerLog.replaced_at.is_(None)
    )
    return set((await session.execute(stmt)).scalars().all())


async def mark_winner_notified(
    session: AsyncSession, winner_id: int, ok: bool = True, error: str | None = None
) -> None:
    """Record the outcome of the winner DM.

    This function existed from day one but nothing called it, so a delivery
    failure was logged and forgotten — there was no way to tell, later, which
    winners had actually been reached.
    """
    w = await session.get(WinnerLog, winner_id)
    if w is not None:
        w.notified_at = _now()
        w.notified_ok = ok
        w.notify_error = (error or "")[:256] or None


async def pending_notifications(session: AsyncSession, giveaway_id: int) -> Sequence[WinnerLog]:
    """Current winners whose DM has not been confirmed delivered."""
    stmt = select(WinnerLog).where(
        WinnerLog.giveaway_id == giveaway_id,
        WinnerLog.replaced_at.is_(None),
        or_(WinnerLog.notified_at.is_(None), WinnerLog.notified_ok.is_(False)),
    )
    stmt = stmt.order_by(WinnerLog.id)
    return (await session.execute(stmt)).scalars().all()


async def replace_tier_winners(session: AsyncSession, giveaway_id: int, tier_name: str) -> None:
    """Mark the previous draw of a tier as superseded (audit trail)."""
    await session.execute(
        update(WinnerLog)
        .where(
            WinnerLog.giveaway_id == giveaway_id,
            WinnerLog.tier_name == tier_name,
            WinnerLog.replaced_at.is_(None),
        )
        .values(replaced_at=_now())
    )


# --------------------------------------------------------------------------
# admins & blacklist
# --------------------------------------------------------------------------


async def is_admin_db(session: AsyncSession, user_id: int) -> bool:
    return (await session.get(AdminUser, user_id)) is not None


async def add_admin(session: AsyncSession, user_id: int, added_by: int | None = None) -> None:
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
        existing.banned_at = _now()
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
        .order_by(Participant.joined_at.desc(), Participant.id.desc())
        .limit(limit)
    )
    return [(p, g) for p, g in (await session.execute(stmt)).all()]
