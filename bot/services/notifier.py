from __future__ import annotations

import logging
from typing import Sequence

from aiogram import Bot

from bot.db.base import get_sessionmaker
from bot.db.models import Giveaway
from bot.i18n import t
from bot.settings import get_settings

log = logging.getLogger("bot.services.notifier")


async def publish_winners(
    bot: Bot,
    giveaway: Giveaway,
    winners: Sequence[tuple[int, str]],
) -> None:
    # 1. Fetch creator and winner users from database in one session
    creator_username = None
    winner_langs: dict[int, str] = {}

    sm = get_sessionmaker()
    async with sm() as session:
        from bot.db.models import User as DBUser
        creator_user = await session.get(DBUser, giveaway.creator_id)
        if creator_user:
            creator_username = creator_user.username

        if winners:
            winner_ids = [w[0] for w in winners]
            from sqlalchemy import select
            stmt = select(DBUser).where(DBUser.user_id.in_(winner_ids))
            res = await session.execute(stmt)
            for u in res.scalars().all():
                if u.lang:
                    winner_langs[u.user_id] = u.lang

    # 2. Publish winners to the original channel/group
    lang = None  # Channel/group posts default to bot default_lang
    if not winners:
        text = t("giveaway.no_participants", lang)
    else:
        lines = [
            t("giveaway.finished_header", lang, id=giveaway.id),
            t("giveaway.winners_header", lang),
        ]
        for user_id, tier in winners:
            mention = f'<a href="tg://user?id={user_id}">{user_id}</a>'
            lines.append(t("giveaway.winner_line", lang, tier=tier, mention=mention))
        text = "\n".join(lines)

    if giveaway.chat_id and giveaway.message_id:
        try:
            await bot.send_message(
                giveaway.chat_id,
                text,
                reply_to_message_id=giveaway.message_id,
            )
        except Exception:
            log.exception("publish_winners: send_message failed")

    # 3. Send private messages (DMs) to winners
    for user_id, tier in winners:
        winner_lang = winner_langs.get(user_id) or get_settings().default_lang

        # Format the creator reference depending on whether they have a username or just an ID
        if creator_username:
            creator_str = f"@{creator_username}"
        else:
            if winner_lang == "zh":
                creator_str = f'<a href="tg://user?id={giveaway.creator_id}">管理员 (ID: {giveaway.creator_id})</a>'
            else:
                creator_str = f'<a href="tg://user?id={giveaway.creator_id}">Host (ID: {giveaway.creator_id})</a>'

        try:
            await bot.send_message(
                user_id,
                t(
                    "winner_dm.body",
                    winner_lang,
                    title=giveaway.title,
                    tier=tier,
                    creator=creator_str,
                ),
            )
        except Exception:
            log.warning("winner DM failed for %d", user_id)
