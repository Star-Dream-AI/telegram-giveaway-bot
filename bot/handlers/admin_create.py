from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.db.base import get_sessionmaker
from bot.db.repo import count_participants
from bot.filters.admin import IsAdmin
from bot.handlers.common import lang_of
from bot.i18n import t
from bot.keyboards.admin import admin_giveaway_panel
from bot.keyboards.participate import participate_keyboard
from bot.services import giveaway as gw_service
from bot.services.channels import validate_channels
from bot.services.scheduler import GiveawayScheduler
from bot.settings import get_settings
from bot.utils.esc import esc_list
from bot.utils.render import render
from bot.utils.timeparse import format_local, parse_channels, parse_end_at, parse_prizes

log = logging.getLogger("bot.handlers.admin_create")


class GiveawayDraft(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_prizes = State()
    waiting_channels = State()
    waiting_duration = State()
    waiting_turnstile = State()
    waiting_target = State()
    waiting_confirm = State()


def build_admin_create_router() -> Router:
    router = Router(name="admin_create")
    router.message.filter(IsAdmin())

    @router.message(Command("cancel"), StateFilter(GiveawayDraft))
    async def on_cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(t("common.cancel", await lang_of(message.from_user.id)))

    @router.message(Command("newgiveaway"))
    async def on_new(message: Message, state: FSMContext) -> None:
        lang = await lang_of(message.from_user.id)
        await state.clear()
        await state.set_state(GiveawayDraft.waiting_title)
        await message.answer(t("fsm.enter_title", lang))

    @router.message(GiveawayDraft.waiting_title)
    async def step_title(message: Message, state: FSMContext) -> None:
        lang = await lang_of(message.from_user.id)
        title = (message.text or "").strip()
        if not title:
            await message.answer(t("fsm.enter_title", lang))
            return
        await state.update_data(title=title[:256])
        await state.set_state(GiveawayDraft.waiting_description)
        await message.answer(t("fsm.enter_description", lang))

    @router.message(GiveawayDraft.waiting_description)
    async def step_description(message: Message, state: FSMContext) -> None:
        lang = await lang_of(message.from_user.id)
        text = (message.text or "").strip()
        description = "" if text == "-" else text[:4096]
        await state.update_data(description=description)
        await state.set_state(GiveawayDraft.waiting_prizes)
        await message.answer(t("fsm.enter_prizes", lang))

    @router.message(GiveawayDraft.waiting_prizes)
    async def step_prizes(message: Message, state: FSMContext) -> None:
        lang = await lang_of(message.from_user.id)
        prizes = parse_prizes(message.text or "")
        if not prizes:
            await message.answer(t("fsm.bad_prizes", lang))
            return
        await state.update_data(prizes=prizes)
        await state.set_state(GiveawayDraft.waiting_channels)
        await message.answer(t("fsm.enter_channels", lang))

    @router.message(GiveawayDraft.waiting_channels)
    async def step_channels(message: Message, state: FSMContext, bot: Bot) -> None:
        lang = await lang_of(message.from_user.id)
        channels = parse_channels(message.text or "")
        if channels:
            # A channel the bot cannot read would make membership checks
            # fail closed for everybody (TelegramAPIError -> "not a member"),
            # so the giveaway could never be won. Catch it at creation.
            missing = await validate_channels(bot, channels)
            if missing:
                await message.answer(t("fsm.bad_channels", lang, channels=esc_list(missing)))
                return
        await state.update_data(channels=channels)
        await state.set_state(GiveawayDraft.waiting_duration)
        await message.answer(t("fsm.enter_duration", lang))

    @router.message(GiveawayDraft.waiting_duration)
    async def step_duration(message: Message, state: FSMContext) -> None:
        lang = await lang_of(message.from_user.id)
        tz = get_settings().tz
        end_at = parse_end_at(message.text or "", tz)
        if end_at is None or end_at <= datetime.now(timezone.utc):
            await message.answer(t("fsm.bad_duration", lang))
            return
        await state.update_data(end_at_iso=end_at.isoformat())
        await state.set_state(GiveawayDraft.waiting_turnstile)
        await message.answer(t("fsm.enter_turnstile", lang))

    @router.message(GiveawayDraft.waiting_turnstile)
    async def step_turnstile(message: Message, state: FSMContext) -> None:
        lang = await lang_of(message.from_user.id)
        text = (message.text or "").strip().lower()
        turn = text in {"yes", "y", "1", "true", "on", "是", "开"}
        if turn and not get_settings().turnstile_capable():
            # Used to be stored blindly: the post advertised a shield and
            # participation hardcoded verified=True, so the promise was a
            # lie. Also PUBLIC_BASE_URL is required to build the verify link.
            await message.answer(t("admin.turnstile_not_configured", lang))
            return
        await state.update_data(turnstile=turn)
        await state.set_state(GiveawayDraft.waiting_target)
        await message.answer(t("fsm.enter_target", lang))

    @router.message(GiveawayDraft.waiting_target)
    async def step_target(message: Message, state: FSMContext, bot: Bot) -> None:
        lang = await lang_of(message.from_user.id)

        target_chat_id: int | None = None
        target_label: str | None = None

        origin = message.forward_origin
        forwarded_chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
        if forwarded_chat is not None:
            target_chat_id = forwarded_chat.id
            target_label = forwarded_chat.title or forwarded_chat.username or str(target_chat_id)
        else:
            text = (message.text or "").strip()
            ref: str | int | None = None
            if text.startswith("@"):
                ref = text
            else:
                try:
                    ref = int(text)
                except ValueError:
                    ref = None
            if ref is not None:
                try:
                    chat = await bot.get_chat(ref)
                    target_chat_id = chat.id
                    target_label = chat.title or chat.username or str(chat.id)
                except Exception:
                    log.warning("get_chat failed for %r", ref)

        if target_chat_id is None:
            await message.answer(t("fsm.bad_target", lang))
            return

        await state.update_data(target_chat_id=target_chat_id, target_label=target_label)

        data = await state.get_data()
        end_at = datetime.fromisoformat(data["end_at_iso"])
        prizes_lines = "\n".join(f"• {p['name']} × {p['count']}" for p in data["prizes"])
        channels_text = " ".join(data["channels"]) if data["channels"] else "-"
        await state.set_state(GiveawayDraft.waiting_confirm)
        await message.answer(
            t(
                "fsm.confirm",
                lang,
                title=data["title"],
                prizes=prizes_lines,
                channels=channels_text,
                ends_at=format_local(end_at, get_settings().tz),
                turnstile=("on" if data.get("turnstile") else "off"),
                target=target_label,
            )
        )

    @router.message(GiveawayDraft.waiting_confirm)
    async def step_confirm(
        message: Message,
        state: FSMContext,
        bot: Bot,
        scheduler: GiveawayScheduler,
    ) -> None:
        lang = await lang_of(message.from_user.id)
        text = (message.text or "").strip().lower()
        if text != "ok":
            await message.answer(t("common.cancel", lang))
            await state.clear()
            return

        data = await state.get_data()
        end_at = datetime.fromisoformat(data["end_at_iso"])
        tz_name = get_settings().tz
        target_chat_id = int(data["target_chat_id"])
        if end_at <= datetime.now(timezone.utc):
            await state.set_state(GiveawayDraft.waiting_duration)
            await message.answer(t("fsm.bad_duration", lang))
            return

        gw = await gw_service.create(
            creator_id=message.from_user.id,
            title=data["title"],
            description=data["description"],
            prize_tiers=data["prizes"],
            required_channels=data["channels"],
            end_at=end_at,
            turnstile_required=bool(data.get("turnstile")),
        )

        sm = get_sessionmaker()
        async with sm() as session:
            count = await count_participants(session, gw.id, verified_only=True)

        post_text = render(
            "giveaway_running",
            lang,
            id=gw.id,
            title=gw.title,
            description=gw.description,
            prizes=gw.prize_tiers,
            channels=gw.required_channels,
            ends_at=format_local(gw.end_at, tz_name),
            count=count,
            turnstile=gw.turnstile_required,
        )

        try:
            sent = await bot.send_message(
                target_chat_id,
                post_text,
                reply_markup=participate_keyboard(gw.id, lang),
            )
        except Exception:
            log.exception("send giveaway post failed: chat_id=%s", target_chat_id)
            # The row is already committed; leaving it "running" with no
            # message and no scheduled draw produces a giveaway that can
            # never be joined and never fires.
            await gw_service.cancel(gw.id)
            await message.answer(t("common.unknown_error", lang))
            await state.clear()
            return

        await gw_service.attach_message(gw.id, sent.chat.id, sent.message_id)
        scheduler.schedule(gw.id, gw.end_at)

        # Admin controls stay in the admin chat on purpose: the public post
        # only carries the join button.
        await message.answer(t("fsm.saved", lang), reply_markup=admin_giveaway_panel(gw.id))
        await state.clear()

    return router
