"""P1-1: a duplicate entry must neither crash nor be reported as success.

add_participant() used to call `await session.rollback()` on IntegrityError,
which discarded every other pending change in the session, while the handler
ignored the return value, committed, and answered "you are in".  A double
tap therefore lost the entry and still confirmed it.
"""

from __future__ import annotations

from sqlalchemy import select

from bot.db.models import User
from bot.db.repo import (
    add_participant,
    count_participants,
    ensure_user,
    has_participated,
)


async def test_first_insert_returns_a_row(sessionmaker, new_giveaway):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        created = await add_participant(
            session, giveaway_id=gid, user_id=1, username="first")
        await session.commit()

    assert created is not None
    assert created.verified is True
    assert created.verified_at is not None


async def test_duplicate_insert_returns_none_instead_of_raising(
    sessionmaker, new_giveaway
):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        first = await add_participant(
            session, giveaway_id=gid, user_id=1, username="a")
        await session.commit()
        second = await add_participant(
            session, giveaway_id=gid, user_id=1, username="a")
        await session.commit()

        assert first is not None
        assert second is None
        assert await count_participants(session, gid) == 1
        assert await has_participated(session, gid, 1) is True


async def test_duplicate_does_not_discard_other_pending_work(
    sessionmaker, new_giveaway
):
    """The SAVEPOINT must confine the failure to the one statement."""
    gid = await new_giveaway()
    async with sessionmaker() as session:
        await add_participant(session, giveaway_id=gid, user_id=7, username="a")
        await session.commit()

        # Stage an unrelated change BEFORE the failing insert.
        await ensure_user(session, 999, username="should_survive")

        again = await add_participant(
            session, giveaway_id=gid, user_id=7, username="a")
        await session.commit()
        assert again is None

    async with sessionmaker() as session:
        users = (
            await session.execute(select(User).where(User.user_id == 999))
        ).scalars().all()

    assert len(users) == 1, "the unrelated user row was rolled back with the dup"


async def test_unverified_rows_keep_their_flag(sessionmaker, new_giveaway):
    """verified=False must not be silently upgraded to True on insert."""
    gid = await new_giveaway()
    async with sessionmaker() as session:
        row = await add_participant(
            session, giveaway_id=gid, user_id=3, username="c", verified=False
        )
        await session.commit()

        assert row is not None
        assert row.verified is False
        assert row.verified_at is None
        assert await count_participants(session, gid) == 0
        assert await count_participants(session, gid, verified_only=False) == 1
