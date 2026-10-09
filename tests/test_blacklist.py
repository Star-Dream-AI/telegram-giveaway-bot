"""P1-3: banning an account must also clear its seats in every pool.

The ban was only ever consulted at join time, so a blacklisted account kept
every entry it had already made and could still be drawn.
"""

from __future__ import annotations

from bot.db.repo import (
    add_participant,
    ban_user,
    blacklist_ids,
    count_participants,
    delete_participations,
    is_blacklisted,
    unban_user,
)


async def test_delete_participations_spans_all_giveaways(
    sessionmaker, new_giveaway
):
    first = await new_giveaway()
    second = await new_giveaway()

    async with sessionmaker() as session:
        await add_participant(session, giveaway_id=first, user_id=1, username="a")
        await add_participant(session, giveaway_id=second, user_id=1, username="a")
        await add_participant(session, giveaway_id=first, user_id=2, username="b")
        await session.commit()

        removed = await delete_participations(session, 1)
        await session.commit()

        assert removed == 2
        assert await count_participants(session, first) == 1
        assert await count_participants(session, second) == 0


async def test_delete_participations_is_idempotent(sessionmaker, new_giveaway):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        assert await delete_participations(session, 12345) == 0


async def test_ban_purge_sequence_matches_the_handler(
    sessionmaker, new_giveaway
):
    # Mirrors bot/handlers/admin_manage.py on_ban().
    gid = await new_giveaway()
    async with sessionmaker() as session:
        await add_participant(session, giveaway_id=gid, user_id=1, username="a")
        await session.commit()

    async with sessionmaker() as session:
        await ban_user(session, 1, reason="abuse", banned_by=42)
        removed = await delete_participations(session, 1)
        await session.commit()

    assert removed == 1
    async with sessionmaker() as session:
        assert await is_blacklisted(session, 1) is True
        assert await count_participants(session, gid) == 0


async def test_unban_clears_the_flag(sessionmaker):
    async with sessionmaker() as session:
        await ban_user(session, 1, reason="x")
        await session.commit()
        assert await is_blacklisted(session, 1) is True

    async with sessionmaker() as session:
        await unban_user(session, 1)
        await session.commit()
        assert await is_blacklisted(session, 1) is False
        assert await blacklist_ids(session, [1]) == set()


async def test_ban_user_refreshes_an_existing_ban(sessionmaker):
    async with sessionmaker() as session:
        await ban_user(session, 1, reason="first")
        await session.commit()

    async with sessionmaker() as session:
        await ban_user(session, 1, reason="second", banned_by=42)
        await session.commit()

    async with sessionmaker() as session:
        from bot.db.models import Blacklist
        from sqlalchemy import select

        rows = (
            await session.execute(select(Blacklist).where(Blacklist.user_id == 1))
        ).scalars().all()

    assert len(rows) == 1, "re-banning must not create a second row"
    assert rows[0].reason == "second"


async def test_blacklist_ids_ignores_absent_users(sessionmaker):
    async with sessionmaker() as session:
        await ban_user(session, 1, reason="x")
        await session.commit()
        assert await blacklist_ids(session, [1, 2, 3]) == {1}
        assert await blacklist_ids(session, []) == set()
