"""P0-1: every timestamp must come back timezone-aware.

Regression guard for the bug that made *every* join button dead: SQLite
returns naive datetimes for DateTime(timezone=True), so
`gw.end_at <= datetime.now(timezone.utc)` raised
`TypeError: can't compare offset-naive and offset-aware datetimes`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bot.db.base import absolutize_sqlite_url, sqlite_path_of
from bot.db.repo import get_giveaway
from bot.db.types import UTCDateTime
from bot.services.scheduler import _as_utc


async def test_end_at_is_aware_after_a_round_trip(sessionmaker, new_giveaway):
    """A deadline written as aware UTC must be read back aware UTC."""
    deadline = datetime.now(timezone.utc) + timedelta(hours=3)
    gid = await new_giveaway(end_at=deadline)

    async with sessionmaker() as session:
        gw = await get_giveaway(session, gid)
        stored = gw.end_at

    assert stored.tzinfo is not None
    assert stored.utcoffset() == timedelta(0)
    # The exact line that used to raise TypeError.
    assert stored > datetime.now(timezone.utc)


async def test_deadline_written_in_another_zone_compares_correctly(
    sessionmaker, new_giveaway
):
    """A giveaway created at 20:00 Asia/Shanghai must expire at the right instant.

    The admin wizard builds the deadline with ZoneInfo(), the scheduler and
    the join handler compare it with datetime.now(timezone.utc).  UTCDateTime
    normalises both sides; a raw DateTime(timezone=True) would not.
    """
    shanghai = ZoneInfo("Asia/Shanghai")
    local = (datetime.now(shanghai) + timedelta(hours=2)).replace(microsecond=0)
    gid = await new_giveaway(end_at=local)

    async with sessionmaker() as session:
        gw = await get_giveaway(session, gid)
        stored = gw.end_at

    assert stored == local.astimezone(timezone.utc)
    assert stored > datetime.now(timezone.utc)


def test_type_decorator_round_trips_both_directions():
    """Unit-level check of the decorator, independent of SQLite."""
    column_type = UTCDateTime()
    aware = datetime(2026, 9, 27, 15, 6, 45, tzinfo=ZoneInfo("Asia/Shanghai"))

    stored = column_type.process_bind_param(aware, None)
    assert stored.tzinfo is None, "storage stays a naive UTC string"
    assert stored.hour == 7

    loaded = column_type.process_result_value(stored, None)
    assert loaded == aware
    assert loaded.tzinfo is not None
    assert column_type.process_bind_param(None, None) is None
    assert column_type.process_result_value(None, None) is None


def test_as_utc_repairs_a_legacy_naive_row():
    """Rows written by the old build are naive; the scheduler must survive them."""
    naive = datetime(2026, 9, 27, 23, 6, 45)
    repaired = _as_utc(naive, giveaway_id=7)

    assert repaired.tzinfo is not None
    assert repaired == naive.replace(tzinfo=timezone.utc)
    # An already-aware value passes through untouched.
    aware = datetime.now(timezone.utc)
    assert _as_utc(aware, giveaway_id=7) is aware


def test_naive_and_aware_now_compare_without_raising():
    """The literal crash from the bug report, as a one-liner."""
    repaired = _as_utc(datetime.utcnow() if False else datetime(2026, 1, 1), 1)
    assert (repaired < datetime.now(timezone.utc)) is True


def test_relative_sqlite_path_resolves_against_the_project_root():
    """A relative DATABASE_URL used to follow the CWD, not the project."""
    resolved = absolutize_sqlite_url("sqlite+aiosqlite:///./data/bot.db")
    assert "/./data/" not in resolved
    assert resolved.endswith("/data/bot.db")
    assert sqlite_path_of(resolved).is_absolute()


def test_memory_and_non_sqlite_urls_are_left_alone():
    for url in ("sqlite+aiosqlite:///:memory:", "postgresql+asyncpg://u@h/db"):
        assert absolutize_sqlite_url(url) == url
    assert sqlite_path_of("postgresql+asyncpg://u@h/db") is None
