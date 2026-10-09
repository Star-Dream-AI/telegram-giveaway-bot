from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from bot.callbacks.factories import AdminGwCb
from bot.db.base import get_sessionmaker
from bot.db.models import GiveawayStatus
from bot.db.repo import (
    add_admin,
    ban_user,
    count_participants,
    delete_participations,
    is_admin_db,
    list_admins,
    list_giveaways_by_creator,
    unban_user,
)
from bot.filters.admin import IsAdmin
from bot.handlers.common import lang_of
from bot.i18n import t
from bot.services import giveaway as gw_service
from bot.services.notifier import retry_pending
from bot.services.scheduler import GiveawayScheduler
from bot.settings import get_settings
from bot.utils.esc import esc, user_mention
from bot.utils.timeparse import format_local

log = logging.getLogger("bot.handlers.admin_manage")


def _int_arg(text: str) -> int | None:
    """First whitespace-separated token as an int, or None."""
    head = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    try:
        return int(head)
    except ValueError:
        return None


async def _is_admin(user_id: int) -> bool:
    """Same two-source check as IsAdmin, for callback handlers."""
    settings = get_settings()
    if user_id in settings.admin_ids:
        return True
    sm = get_sessionmaker()
    async with sm() as session:
        return await is_admin_db(session, user_id)


def build_admin_manage_router() -> Router:
    router = Router(name="admin_manage")
    router.message.filter(IsAdmin())

    @router.callback_query(AdminGwCb.filter())
    async def on_panel(
        cq: CallbackQuery,
        callback_data: AdminGwCb,
        bot: Bot,
        scheduler: GiveawayScheduler,
    ) -> None:
        # admin_giveaway_panel() and this factory shipped with no handler at
        # all, so the cancel button raised a callback timeout. Note that the
        # message filter above is message-only: aiogram does not carry it over
        # to callbacks, hence the explicit re-check.
        if not await _is_admin(cq.from_user.id):
            lang = await lang_of(cq.from_user.id)
            await cq.answer(t("common.not_admin", lang), show_alert=True)
            return
        if callback_data.action != "cancel":
            await cq.answer()
            return
        lang = await lang_of(cq.from_user.id)
        gid = callback_data.giveaway_id
        if await gw_service.cancel(gid):
            scheduler.cancel(gid)
            await cq.answer(t("admin.cancel_ok", lang, id=gid))
        else:
            await cq.answer(t("admin.cancel_missing", lang, id=gid), show_alert=True)

    @router.message(Command("list"))
    async def on_list(message: Message) -> None:
        lang = await lang_of(message.from_user.id)
        sm = get_sessionmaker()
        async with sm() as session:
            # creator_id=None: every admin sees every running giveaway, which
            # is what the README promised and what /cancel + /reroll act on.
            running = await list_giveaways_by_creator(session, status=GiveawayStatus.running)
            counts = {gw.id: await count_participants(session, gw.id) for gw in running}
        if not running:
            await message.answer(t("admin.list_empty", lang))
            return
        tz = get_settings().tz
        lines = [t("admin.list_header", lang, count=len(running))]
        for gw in running:
            lines.append(
                t(
                    "admin.list_row",
                    lang,
                    id=gw.id,
                    title=gw.title,
                    ends_at=format_local(gw.end_at, tz),
                    count=counts[gw.id],
                )
            )
        await message.answer("\n".join(lines))

    @router.message(Command("cancel"))
    async def on_cancel(
        message: Message, command: CommandObject, scheduler: GiveawayScheduler
    ) -> None:
        lang = await lang_of(message.from_user.id)
        if not command.args:
            await message.answer(t("admin.usage_cancel", lang))
            return
        gid = _int_arg(command.args)
        if gid is None:
            await message.answer(t("admin.usage_cancel", lang))
            return
        ok = await gw_service.cancel(gid)
        if ok:
            scheduler.cancel(gid)
            await message.answer(t("admin.cancel_ok", lang, id=gid))
        else:
            await message.answer(t("admin.cancel_missing", lang, id=gid))

    @router.message(Command("reroll"))
    async def on_reroll(message: Message, command: CommandObject, bot: Bot) -> None:
        lang = await lang_of(message.from_user.id)
        if not command.args:
            await message.answer(t("admin.usage_reroll", lang))
            return
        parts = command.args.split(maxsplit=1)
        if len(parts) < 2 or not parts[0].isdigit():
            await message.answer(t("admin.usage_reroll", lang))
            return
        gid = int(parts[0])
        tier = parts[1].strip()
        try:
            picks, shortfall = await gw_service.reroll_tier(gid, tier, bot)
        except gw_service.RerollError as exc:
            key = {
                "running": "admin.reroll_running",
                "canceled": "admin.reroll_canceled",
                "no_such_tier": "admin.reroll_no_tier",
            }.get(str(exc), "admin.reroll_empty")
            await message.answer(t(key, lang, id=gid, tier=esc(tier)))
            return
        if not picks:
            await message.answer(t("admin.reroll_empty", lang, id=gid, tier=esc(tier)))
            return
        await message.answer(t("admin.reroll_ok", lang, id=gid, tier=esc(tier), count=len(picks)))

    @router.message(Command("ban"))
    async def on_ban(message: Message, command: CommandObject) -> None:
        lang = await lang_of(message.from_user.id)
        if not command.args:
            await message.answer(t("admin.usage_ban", lang))
            return
        parts = command.args.split(maxsplit=1)
        uid = _int_arg(parts[0])
        if uid is None:
            await message.answer(t("admin.usage_ban", lang))
            return
        reason = parts[1].strip() if len(parts) > 1 else None
        sm = get_sessionmaker()
        async with sm() as session:
            await ban_user(session, uid, reason=reason, banned_by=message.from_user.id)
            # P1-3: the ban used to be enforced only at join time, so a user
            # blacklisted mid-giveaway kept their seat and could still win.
            removed = await delete_participations(session, uid)
            await session.commit()
        key = "admin.ban_ok" if removed else "admin.ban_ok_none"
        await message.answer(t(key, lang, user_id=uid, n=removed))

    @router.message(Command("retry"))
    async def on_retry(message: Message, command: CommandObject, bot: Bot) -> None:
        lang = await lang_of(message.from_user.id)
        gid = _int_arg(command.args or "")
        if gid is None or gid <= 0:
            await message.answer(t("admin.usage_retry", lang))
            return
        count = await retry_pending(bot, gid)
        await message.answer(t("admin.retry_ok", lang, id=gid, count=count))

    @router.message(Command("unban"))
    async def on_unban(message: Message, command: CommandObject) -> None:
        lang = await lang_of(message.from_user.id)
        uid = _int_arg(command.args or "")
        if uid is None:
            await message.answer(t("admin.usage_unban", lang))
            return
        sm = get_sessionmaker()
        async with sm() as session:
            await unban_user(session, uid)
            await session.commit()
        await message.answer(t("admin.unban_ok", lang, user_id=uid))

    @router.message(Command("addadmin"))
    async def on_addadmin(message: Message, command: CommandObject) -> None:
        lang = await lang_of(message.from_user.id)
        uid = _int_arg(command.args or "")
        if uid is None:
            await message.answer(t("admin.usage_addadmin", lang))
            return
        sm = get_sessionmaker()
        async with sm() as session:
            await add_admin(session, uid, added_by=message.from_user.id)
            await session.commit()
        await message.answer(t("admin.addadmin_ok", lang, user_id=user_mention(uid)))

    @router.message(Command("admins"))
    async def on_admins(message: Message) -> None:
        # Reuses the AdminGwCb panel entry point that used to be dead code.
        lang = await lang_of(message.from_user.id)
        sm = get_sessionmaker()
        async with sm() as session:
            admins = await list_admins(session)
        if not admins:
            await message.answer(t("admin.list_empty", lang))
            return
        lines = [t("admin.list_header", lang, count=len(admins))]
        for row in admins:
            lines.append("• " + user_mention(row.user_id))
        await message.answer("\n".join(lines))

    return router
