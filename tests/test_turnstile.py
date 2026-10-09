"""P0-2: the Turnstile proof is real, short lived, and spends once.

Before the fix there was no siteverify call, no verify page, and
add_participant(..., verified=True) was hard coded, so the captcha shown in
the giveaway post protected nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from bot.db.repo import get_giveaway, list_participants
from bot.db.turnstile_repo import (
    PROOF_TTL,
    consume_proof,
    has_valid_proof,
    hash_token,
    issue_proof,
    new_nonce,
    purge_expired,
)


async def test_only_the_token_hash_is_stored(sessionmaker, new_giveaway):
    gid = await new_giveaway()
    token = "the-raw-cloudflare-token"

    async with sessionmaker() as session:
        proof = await issue_proof(session, user_id=5, giveaway_id=gid, token=token)
        await session.commit()

        from bot.db.models import TurnstileProof

        row = (
            await session.execute(
                select(TurnstileProof).where(TurnstileProof.nonce == proof.nonce)
            )
        ).scalar_one()

    assert token not in row.token_hash
    assert row.token_hash == hash_token(token)
    assert len(row.token_hash) == 64


async def test_nonce_is_unique_and_url_safe():
    nonces = {new_nonce() for _ in range(50)}
    assert len(nonces) == 50
    assert all(n.replace("-", "").replace("_", "").isalnum() for n in nonces)


async def test_proof_can_be_consumed_exactly_once(sessionmaker, new_giveaway):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        await issue_proof(session, user_id=6, giveaway_id=gid, token="tok")
        await session.commit()

    async with sessionmaker() as session:
        assert await consume_proof(session, user_id=6, giveaway_id=gid) is True
        await session.commit()

    async with sessionmaker() as session:
        assert await consume_proof(session, user_id=6, giveaway_id=gid) is False
        assert await has_valid_proof(session, 6, gid) is False


async def test_proof_is_bound_to_one_user_and_one_giveaway(
    sessionmaker, new_giveaway
):
    """Sharing a solved link must not mint an entry for someone else."""
    gid = await new_giveaway()
    other = await new_giveaway()

    async with sessionmaker() as session:
        await issue_proof(session, user_id=6, giveaway_id=gid, token="tok")
        await session.commit()

    async with sessionmaker() as session:
        assert await consume_proof(session, user_id=7, giveaway_id=gid) is False
        assert await consume_proof(session, user_id=6, giveaway_id=other) is False
        # The original owner can still use it.
        assert await has_valid_proof(session, 6, gid) is True
        assert await consume_proof(session, user_id=6, giveaway_id=gid) is True


async def test_expired_proof_is_rejected(sessionmaker, new_giveaway):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        await issue_proof(
            session,
            user_id=8,
            giveaway_id=gid,
            token="tok",
            ttl=timedelta(seconds=-1),
        )
        await session.commit()

    async with sessionmaker() as session:
        assert await has_valid_proof(session, 8, gid) is False
        assert await consume_proof(session, user_id=8, giveaway_id=gid) is False


async def test_default_ttl_is_ten_minutes(sessionmaker, new_giveaway):
    gid = await new_giveaway()
    assert PROOF_TTL == timedelta(minutes=10)

    before = datetime.now(timezone.utc)
    async with sessionmaker() as session:
        proof = await issue_proof(session, user_id=9, giveaway_id=gid, token="tok")
        await session.commit()

    assert before + timedelta(minutes=9) <= proof.expires_at
    assert proof.expires_at <= before + timedelta(minutes=11)


async def test_purge_removes_only_stale_rows(sessionmaker, new_giveaway):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        await issue_proof(
            session,
            user_id=10,
            giveaway_id=gid,
            token="fresh",
        )
        await issue_proof(
            session,
            user_id=11,
            giveaway_id=gid,
            token="stale",
            ttl=timedelta(seconds=-1),
        )
        await session.commit()

        assert await purge_expired(session) == 1
        await session.commit()

    async with sessionmaker() as session:
        assert await has_valid_proof(session, 10, gid) is True
        assert await has_valid_proof(session, 11, gid) is False


async def test_unverified_entries_never_reach_the_pool(
    sessionmaker, new_giveaway
):
    """The draw queries verified rows only."""
    from bot.db.repo import add_participant

    gid = await new_giveaway()
    async with sessionmaker() as session:
        await add_participant(session, giveaway_id=gid, user_id=1, username="ok")
        await add_participant(
            session, giveaway_id=gid, user_id=2, username="no", verified=False
        )
        await session.commit()

        pool = await list_participants(session, gid, verified_only=True)

    assert [p.user_id for p in pool] == [1]
