from __future__ import annotations

from bot.db.base import get_sessionmaker
from bot.db.repo import get_user_lang
from bot.settings import get_settings


async def lang_of(user_id: int) -> str:
    sm = get_sessionmaker()
    async with sm() as session:
        lang = await get_user_lang(session, user_id)
    return lang or get_settings().default_lang
