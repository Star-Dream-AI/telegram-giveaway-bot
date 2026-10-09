from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from bot.settings import PROJECT_ROOT

log = logging.getLogger("bot.db")


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _sessionmaker


# Columns added after the first release.  ``create_all()`` can only create
# *new* tables; it never alters existing ones.  This is the smallest
# sufficient migration mechanism for this project — no Alembic, no ceremony,
# just "add the column if the table is missing it".
_LIGHTWEIGHT_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # (table, column, DDL type)
    ("winner_logs", "replaced_at", "DATETIME"),
    ("winner_logs", "notified_ok", "BOOLEAN"),
    ("winner_logs", "notify_error", "VARCHAR(256)"),
    ("participants", "verified_at", "DATETIME"),
    ("participants", "turnstile_nonce", "VARCHAR(64)"),
    ("participants", "turnstile_expires_at", "DATETIME"),
)


def absolutize_sqlite_url(database_url: str) -> str:
    """Resolve a relative SQLite path against the project root.

    ``.env`` is always read from ``PROJECT_ROOT/.env``, but the
    ``database_url`` inside it was previously resolved against the current
    working directory — so starting the bot from a systemd unit, a cron job or
    a double-clicked file created the database in a different, surprising
    place.  Relative paths now always mean "relative to the project root".
    """
    prefix, sep, rest = database_url.partition(":///")
    if not sep:
        return database_url
    path, qsep, query = rest.partition("?")
    if not path or path.startswith(":memory:") or path == "":
        return database_url
    candidate = Path(path[2:] if path.startswith("./") else path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.as_posix()
    return f"{prefix}:///{resolved}" + (f"?{query}" if qsep else "")


def sqlite_path_of(database_url: str) -> Path | None:
    """Filesystem path behind a SQLite URL, or None for other backends."""
    if not database_url.startswith("sqlite"):
        return None
    _prefix, sep, rest = database_url.partition(":///")
    if not sep:
        return None
    path, _qsep, _query = rest.partition("?")
    if not path or path.startswith(":memory:"):
        return None
    return Path(path)


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Apply the SQLite pragmas on **every** connection.

    The previous code ran them inside ``engine.begin()``, i.e. on the one
    connection that happened to be checked out at that moment.  aiosqlite uses
    a ``NullPool``, so each session gets a brand new connection with
    default settings — measured: ``PRAGMA foreign_keys`` read back as 0 on
    the very next connection.  ``journal_mode=WAL`` happens to be persistent
    (it is a database-level property) so it survived, but ``foreign_keys`` is
    per-connection and did not.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    @event.listens_for(engine.sync_engine, "connect")
    def _verify_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys")
            if cursor.fetchone()[0] != 1:
                log.warning("SQLite foreign_keys pragma did not take effect")
        except Exception:
            log.exception("could not verify SQLite pragmas")
        finally:
            cursor.close()


async def _apply_lightweight_migrations(conn) -> None:
    for table, column, ddl_type in _LIGHTWEIGHT_MIGRATIONS:
        try:
            result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
            existing = {row[1] for row in result.fetchall()}
        except Exception:
            log.exception("migration probe failed for %s", table)
            continue
        if not existing:
            # Table is new; create_all() will create it with the new columns.
            continue
        if column in existing:
            continue
        await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
        log.info("migration: added %s.%s", table, column)


async def init_db(database_url: str) -> None:
    global _engine, _sessionmaker

    database_url = absolutize_sqlite_url(database_url)
    db_path = sqlite_path_of(database_url)
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("sqlite path: %s", db_path)

    _engine = create_async_engine(database_url, future=True, echo=False)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

    if database_url.startswith("sqlite"):
        _install_sqlite_pragmas(_engine)

    from bot.db import models  # noqa: F401

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if database_url.startswith("sqlite"):
            await _apply_lightweight_migrations(conn)
    log.info("db ready: %s", database_url.split("://", 1)[0])
