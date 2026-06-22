from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.db.base import get_sessionmaker
from bot.db.models import GiveawayStatus
from bot.db.repo import (
    add_admin,
    ban_user,
    list_giveaways_by_creator,
    unban_user,
)
from bot.filters.admin import IsAdmin
from bot.handlers.common import lang_of
from bot.i18n import t
from bot.services import giveaway as gw_service
from bot.services.scheduler import GiveawayScheduler
from bot.settings import get_settings
from bot.utils.timeparse import format_local

log = logging.getLogger("bot.handlers.admin_manage")


def build_admin_manage_router() -> Router:
    router = Router(name="admin_manage")
    router.message.filter(IsAdmin())

    @router.message(Command("list"))
    async def on_list(message: Message) -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            running = await list_giveaways_by_creator(
                session, message.from_user.id, status=GiveawayStatus.running
            )
        if not running:
            await message.answer("(no running giveaways)")
            return
        tz = get_settings().tz
        lines = []
        for gw in running:
            lines.append(
                f"#{gw.id} {gw.title} → {format_local(gw.end_at, tz)}"
            )
        await message.answer("\n".join(lines))

    @router.message(Command("cancel"))
    async def on_cancel(
        message: Message, command: CommandObject, scheduler: GiveawayScheduler
    ) -> None:
        if not command.args:
            await message.answer("usage: /cancel <id>")
            return
        try:
            gid = int(command.args.strip().split()[0])
        except ValueError:
            await message.answer("usage: /cancel <id>")
            return
        ok = await gw_service.cancel(gid)
        if ok:
            scheduler.cancel(gid)
            lang = await lang_of(message.from_user.id)
            await message.answer(t("giveaway.canceled", lang, id=gid))
        else:
            await message.answer(f"#{gid} not running or not found")

    @router.message(Command("reroll"))
    async def on_reroll(
        message: Message, command: CommandObject, bot: Bot
    ) -> None:
        if not command.args:
            await message.answer("usage: /reroll <id> <tier_name>")
            return
        parts = command.args.split(maxsplit=1)
        if len(parts) < 2 or not parts[0].isdigit():
            await message.answer("usage: /reroll <id> <tier_name>")
            return
        gid = int(parts[0])
        tier = parts[1].strip()
        picks = await gw_service.reroll_tier(gid, tier, bot)
        if not picks:
            await message.answer(f"#{gid} reroll failed (no participants or tier)")
        else:
            await message.answer(f"#{gid} re-rolled {tier}: {len(picks)} winner(s)")

    @router.message(Command("ban"))
    async def on_ban(message: Message, command: CommandObject) -> None:
        if not command.args:
            await message.answer("usage: /ban <user_id> [reason]")
            return
        parts = command.args.split(maxsplit=1)
        if not parts[0].lstrip("-").isdigit():
            await message.answer("usage: /ban <user_id> [reason]")
            return
        uid = int(parts[0])
        reason = parts[1].strip() if len(parts) > 1 else None
        sm = get_sessionmaker()
        async with sm() as session:
            await ban_user(session, uid, reason=reason, banned_by=message.from_user.id)
            await session.commit()
        await message.answer(f"banned {uid}")

    @router.message(Command("unban"))
    async def on_unban(message: Message, command: CommandObject) -> None:
        if not command.args or not command.args.strip().lstrip("-").isdigit():
            await message.answer("usage: /unban <user_id>")
            return
        uid = int(command.args.strip())
        sm = get_sessionmaker()
        async with sm() as session:
            await unban_user(session, uid)
            await session.commit()
        await message.answer(f"unbanned {uid}")

    @router.message(Command("addadmin"))
    async def on_addadmin(message: Message, command: CommandObject) -> None:
        if not command.args or not command.args.strip().lstrip("-").isdigit():
            await message.answer("usage: /addadmin <user_id>")
            return
        uid = int(command.args.strip())
        sm = get_sessionmaker()
        async with sm() as session:
            await add_admin(session, uid, added_by=message.from_user.id)
            await session.commit()
        await message.answer(f"admin added: {uid}")

    return router
