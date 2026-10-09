"""Required-channel enforcement.

``required_channels`` used to be stored and printed and then... nothing.
There was not a single ``get_chat_member`` call in the project, so the
"join the channels to enter" promise in the README and in the giveaway
post was pure decoration.  The locale string for the failure case existed
and was never reachable.

The check is deliberately **fail-closed**: a channel the bot cannot inspect
(it was demoted, or the admin typo-ed a username) is reported as missing,
because the alternative — silently waving everybody through — would be a
quiet, permanent hole in the entry rules.  That failure is loud and
visible instead, and ``validate_channels()`` is called when the giveaway is
created so the problem is reported to the admin before anybody tries to
enter.
"""

from __future__ import annotations

import logging
from typing import Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

log = logging.getLogger("bot.services.channels")


def _normalise(ref: str) -> str:
    ref = ref.strip()
    if ref.startswith("https://t.me/"):
        ref = ref.rstrip("/").rsplit("/", 1)[-1]
    return ref if ref.startswith("@") else f"@{ref}"


async def missing_channels(bot: Bot, user_id: int, channels: Sequence[str]) -> tuple[str, ...]:
    """Channels of ``channels`` that ``user_id`` has not joined.

    An empty result means the user satisfies every requirement.
    """
    if not channels:
        return ()
    missing: list[str] = []
    for raw in channels:
        ref = _normalise(raw)
        if not len(ref) > 1:
            continue
        try:
            member = await bot.get_chat_member(ref, user_id)
        except TelegramAPIError as exc:
            # Includes "bot is not a member" and "chat not found": we cannot
            # prove membership, so we do not grant it.
            log.warning("channel check failed for %s: %s", ref, exc)
            missing.append(ref)
            continue
        status = str(getattr(member.status, "value", member.status) or "")
        if status in {"member", "administrator", "owner", "creator"}:
            continue
        if status in {"left", "kicked"}:
            missing.append(ref)
            continue
        if status == "restricted" and getattr(member, "is_member", False):
            continue
        missing.append(ref)
    return tuple(missing)


async def validate_channels(bot: Bot, channels: Sequence[str]) -> tuple[str, ...]:
    """Creation-time check: can the bot actually see every required channel?

    Returns a tuple of human-readable problems, empty when the giveaway is
    safe to create.  The bot must be a member (administrator is better) or
    the entry gate cannot be enforced.
    """
    problems: list[str] = []
    for raw in channels:
        ref = _normalise(raw)
        if not len(ref) > 1:
            problems.append(str(raw))
            continue
        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(ref, me.id)
        except TelegramAPIError as exc:
            problems.append(f"{ref} ({exc})")
            continue
        status = str(getattr(member.status, "value", member.status) or "")
        if status not in {"administrator", "creator", "owner"}:
            problems.append(f"{ref} (bot must be an administrator)")
    return tuple(problems)
