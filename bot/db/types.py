from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """A datetime column that is **always** timezone-aware UTC in Python.

    SQLite has no native timezone-aware type: it stores datetimes as plain
    strings and hands them back naive.  A bare ``DateTime(timezone=True)``
    therefore returns naive objects on read, and comparing one against an
    aware value raises::

        TypeError: can't compare offset-naive and offset-aware datetimes

    That is not a theoretical concern: ``participate.py`` compared
    ``gw.end_at <= datetime.now(timezone.utc)``, so *every* join button
    failed, and ``scheduler.restore_from_db()`` raised for every pending
    giveaway on startup.

    This decorator keeps the storage format unchanged (naive UTC strings) but
    normalises to UTC in both directions, so a value written as
    ``datetime.now(timezone.utc)`` compares correctly with one built from
    ``ZoneInfo(...)``.  Use it for every timestamp column in the project.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            # Naive input is stored verbatim; it is already UTC by convention.
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
