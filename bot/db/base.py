from __future__ import annotations

import logging
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

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


async def init_db(database_url: str) -> None:
    global _engine, _sessionmaker

    if database_url.startswith("sqlite"):
        import os
        # Extract path from sqlite+aiosqlite:///path/to/file or sqlite:///path/to/file
        sep = ":///"
        if sep in database_url:
            db_path = database_url.split(sep, 1)[1].split("?", 1)[0]
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

    _engine = create_async_engine(database_url, future=True, echo=False)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

    from bot.db import models  # noqa: F401

    async with _engine.begin() as conn:
        if database_url.startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)
    log.info("db ready: %s", database_url.split("://", 1)[0])


async def session_scope() -> AsyncIterator[AsyncSession]:
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
