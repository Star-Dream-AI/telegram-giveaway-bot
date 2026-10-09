"""P1-2: PRAGMA foreign_keys=ON has to be on every connection.

aiosqlite uses a NullPool, so pragmas executed once inside engine.begin()
applied to exactly one connection and the next session silently ran with
foreign keys OFF.  The fix registers a `connect` event listener.
"""

from __future__ import annotations

from sqlalchemy import text

from bot.db.base import get_engine


async def test_foreign_keys_is_on_for_a_fresh_connection(sessionmaker):
    engine = get_engine()
    async with engine.connect() as conn:
        enabled = (await conn.execute(text("PRAGMA foreign_keys"))).scalar()
    assert enabled == 1


async def test_foreign_keys_survives_opening_a_second_connection(sessionmaker):
    """This is the case the old one-shot implementation failed."""
    engine = get_engine()
    async with engine.connect() as first:
        pass
    async with engine.connect() as second:
        assert (await second.execute(text("PRAGMA foreign_keys"))).scalar() == 1
        assert (await second.execute(text("PRAGMA journal_mode"))).scalar() == "wal"


async def test_cascade_delete_is_actually_enforced(sessionmaker, new_giveaway):
    """With foreign keys off, deleting a giveaway would orphan its rows."""
    from bot.db.repo import add_participant

    gid = await new_giveaway()
    async with sessionmaker() as session:
        await add_participant(session, giveaway_id=gid, user_id=1, username="a")
        await session.commit()
        await session.execute(text("DELETE FROM giveaways WHERE id = :i"), {"i": gid})
        await session.commit()
        remaining = (
            await session.execute(text("SELECT COUNT(*) FROM participants"))
        ).scalar()
    assert remaining == 0
