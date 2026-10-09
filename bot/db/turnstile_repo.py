"""Persistence for Cloudflare Turnstile proofs.

Kept in its own module so ``repo.py`` stays a readable catalogue instead of
growing a second concern.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import TurnstileProof

PROOF_TTL = timedelta(minutes=10)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


@dataclass(frozen=True, slots=True)
class StoredProof:
    user_id: int
    giveaway_id: int
    nonce: str
    expires_at: datetime


async def issue_proof(
    session: AsyncSession,
    *,
    user_id: int,
    giveaway_id: int,
    token: str,
    remote_ip: str | None = None,
    ttl: timedelta = PROOF_TTL,
) -> StoredProof:
    """Record that ``user_id`` solved the captcha for ``giveaway_id``."""
    now = datetime.now(timezone.utc)
    nonce = new_nonce()
    proof = TurnstileProof(
        user_id=user_id,
        giveaway_id=giveaway_id,
        nonce=nonce,
        token_hash=hash_token(token),
        remote_ip=(remote_ip or "")[:45] or None,
        created_at=now,
        expires_at=now + ttl,
    )
    session.add(proof)
    await session.flush()
    return StoredProof(
        user_id=user_id, giveaway_id=giveaway_id, nonce=nonce, expires_at=proof.expires_at
    )


async def consume_proof(
    session: AsyncSession, *, user_id: int, giveaway_id: int, nonce: str | None = None
) -> bool:
    """Atomically spend a proof. Returns False if missing, stale or already used.

    ``nonce`` is optional on purpose.  The nonce travels on the ``t.me`` deep
    link, but the join button the user finally presses lives in the channel
    post and carries no nonce at all, so the handler spends "this user's live
    proof for this giveaway".  Passing the nonce when one is available keeps
    the tighter, nonce-pinned behaviour.

    The ``UPDATE ... WHERE consumed_at IS NULL`` is what makes concurrent
    double-submits safe: the second one updates zero rows.
    """
    now = datetime.now(timezone.utc)
    candidate = (
        select(TurnstileProof.id)
        .where(
            TurnstileProof.user_id == user_id,
            TurnstileProof.giveaway_id == giveaway_id,
            TurnstileProof.consumed_at.is_(None),
            TurnstileProof.expires_at > now,
        )
        .order_by(TurnstileProof.created_at, TurnstileProof.id)
        .limit(1)
    )
    if nonce is not None:
        candidate = candidate.where(TurnstileProof.nonce == nonce)
    stmt = (
        update(TurnstileProof)
        .where(
            TurnstileProof.id == candidate.scalar_subquery(),
            TurnstileProof.consumed_at.is_(None),
            TurnstileProof.expires_at > now,
        )
        .values(consumed_at=now)
        .execution_options(synchronize_session=False)
    )
    return (await session.execute(stmt)).rowcount == 1


async def has_valid_proof(
    session: AsyncSession, user_id: int, giveaway_id: int, *, nonce: str | None = None
) -> bool:
    now = datetime.now(timezone.utc)
    stmt = (
        select(TurnstileProof.id)
        .where(
            TurnstileProof.user_id == user_id,
            TurnstileProof.giveaway_id == giveaway_id,
            TurnstileProof.consumed_at.is_(None),
            TurnstileProof.expires_at > now,
        )
        .limit(1)
    )
    if nonce is not None:
        stmt = stmt.where(TurnstileProof.nonce == nonce)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def purge_expired(session: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    result = await session.execute(delete(TurnstileProof).where(TurnstileProof.expires_at < now))
    return int(result.rowcount or 0)
