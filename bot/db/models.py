from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Index,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base
from bot.db.types import UTCDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GiveawayStatus(str, enum.Enum):
    running = "running"
    finished = "finished"
    canceled = "canceled"


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    lang: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class Giveaway(Base):
    __tablename__ = "giveaways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    creator_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(String(4096), default="")
    prize_tiers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    required_channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    # UTCDateTime, not DateTime(timezone=True): SQLite returns naive values for
    # the latter, which made every aware/naive comparison blow up.  See
    # bot/db/types.py.
    end_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[GiveawayStatus] = mapped_column(
        SAEnum(GiveawayStatus, name="giveaway_status", native_enum=False),
        default=GiveawayStatus.running,
        index=True,
    )
    turnstile_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)

    participants: Mapped[list["Participant"]] = relationship(
        back_populates="giveaway", cascade="all, delete-orphan"
    )
    winners: Mapped[list["WinnerLog"]] = relationship(
        back_populates="giveaway", cascade="all, delete-orphan"
    )


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("giveaway_id", "user_id", name="uq_participant_giveaway_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    # Stores the SHA-256 of a consumed Turnstile token, never the token
    # itself.  Turnstile tokens are single-use and ~2KB; keeping raw tokens
    # around would be a needless secret-at-rest problem.
    turnstile_token: Mapped[str | None] = mapped_column(String(64))
    turnstile_nonce: Mapped[str | None] = mapped_column(String(64))
    turnstile_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    giveaway: Mapped[Giveaway] = relationship(back_populates="participants")


class WinnerLog(Base):
    __tablename__ = "winner_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    tier_name: Mapped[str] = mapped_column(String(64))
    drawn_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Audit trail for /reroll.  Rows carrying a replaced_at timestamp are
    # superseded draws and are excluded from the current winner set.
    replaced_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    # Delivery bookkeeping: without this a failed winner DM was invisible and
    # unretryable, even though mark_winner_notified() already existed.
    notified_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_error: Mapped[str | None] = mapped_column(String(256))

    giveaway: Mapped[Giveaway] = relationship(back_populates="winners")

    __table_args__ = (
        Index("ix_winner_logs_current", "giveaway_id", "tier_name", "replaced_at"),
    )


class AdminUser(Base):
    __tablename__ = "admin_users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    added_by: Mapped[int | None] = mapped_column(BigInteger)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class TurnstileProof(Base):
    """A short-lived, single-use proof that one user solved the captcha.

    Cloudflare tokens are single-use, so this table mostly exists to (a) bind
    a solved challenge to a concrete giveaway and user, (b) make the window
    short, and (c) survive a Cloudflare hiccup without letting a token be
    spent twice.  Only the token's SHA-256 is stored.
    """

    __tablename__ = "turnstile_proofs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    giveaway_id: Mapped[int] = mapped_column(ForeignKey("giveaways.id", ondelete="CASCADE"), index=True)
    nonce: Mapped[str] = mapped_column(String(64), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    remote_ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Blacklist(Base):
    __tablename__ = "blacklist"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reason: Mapped[str | None] = mapped_column(String(512))
    banned_by: Mapped[int | None] = mapped_column(BigInteger)
    banned_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
