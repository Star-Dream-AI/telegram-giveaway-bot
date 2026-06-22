from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.db.base import get_sessionmaker
from bot.db.repo import is_admin_db
from bot.settings import get_settings


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        uid = message.from_user.id if message.from_user else 0
        if uid in get_settings().admin_ids:
            return True
        sm = get_sessionmaker()
        async with sm() as session:
            return await is_admin_db(session, uid)
