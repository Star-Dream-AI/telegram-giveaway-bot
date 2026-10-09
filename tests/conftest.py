from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramAPIError

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# bot.settings validates BOT_TOKEN at import time and reads .env from the
# project root.  Give the suite a throwaway environment so it never depends
# on a real deployment file and never writes into one.
os.environ.setdefault("BOT_TOKEN", "123456789:AAHunit-test-token-not-real")
os.environ.pop("WEBHOOK_BASE_URL", None)
os.environ.pop("WEBHOOK_SECRET", None)

from bot.db import base as db_base  # noqa: E402
from bot.db.repo import create_giveaway  # noqa: E402
from bot.i18n import load_locales  # noqa: E402

ADMIN_ID = 424242
BOT_ID = 777000
PARTICIPANT_IDS = [1001, 1002, 1003, 1004, 1005]


@pytest.fixture(scope="session", autouse=True)
def _locales() -> None:
    """Every handler formats text through t(), so the tables must be loaded."""
    load_locales("zh")


class FakeBot:
    """Minimal Bot stand-in.

    Records outbound sends so a test can assert on the published text, can be
    told to fail delivery for chosen users, and answers getChatMember from an
    explicit table so channel enforcement is exercised without a network.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[int, str, dict]] = []
        self.fail_for: set[int] = set()
        self.members: dict[tuple[str, int], str] = {}

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if chat_id in self.fail_for:
            raise TelegramAPIError("sendMessage", "Forbidden: bot was blocked by the user")
        self.sent.append((chat_id, text, kwargs))

    async def get_chat_member(self, chat_id: str, user_id: int):
        key = (str(chat_id), int(user_id))
        if key not in self.members:
            raise TelegramAPIError("getChatMember", "chat not found")
        return SimpleNamespace(status=self.members[key])

    async def get_me(self):
        return SimpleNamespace(id=BOT_ID, username="giveaway_test_bot")

    def set_member(self, ref: str, user_id: int, status: str) -> None:
        self.members[(str(ref), int(user_id))] = status

    def texts(self) -> list[str]:
        return [text for _chat, text, _kw in self.sent]


@pytest.fixture
def bot() -> FakeBot:
    return FakeBot()


@pytest_asyncio.fixture
async def sessionmaker(tmp_path):
    """A private on-disk SQLite database per test, wired through init_db()."""
    url = "sqlite+aiosqlite:///" + (tmp_path / "test.db").as_posix()
    await db_base.init_db(url)
    sm = db_base.get_sessionmaker()
    try:
        yield sm
    finally:
        await db_base.get_engine().dispose()
        db_base._engine = None
        db_base._sessionmaker = None


@pytest_asyncio.fixture
async def new_giveaway(sessionmaker):
    """Factory: persist a giveaway with sane defaults, return its id."""

    async def _make(**overrides) -> int:
        params = {
            "creator_id": ADMIN_ID,
            "title": "R&D key",
            "description": "",
            "prize_tiers": [{"name": "Steam card", "count": 1}],
            "required_channels": [],
            "end_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "turnstile_required": False,
        }
        params.update(overrides)
        async with sessionmaker() as session:
            gw = await create_giveaway(session, **params)
            await session.commit()
            return gw.id

    return _make
