"""HTML escaping for Telegram's HTML parse mode.

The bot runs with ``ParseMode.HTML`` for every outbound message, so any
user-supplied text that lands in a message body has to be escaped or Telegram
rejects the whole message with ``BadRequest: can't parse entities``.  The
old code escaped nothing on the i18n path: a giveaway titled ``R&D key`` or
``5<10% off`` made ``/newgiveaway``, ``/me`` and the winner DM all fail.

Two rules make this safe without sprinkling ``html.escape`` at 40 call sites:

1. ``t()`` escapes **every** interpolation value.
2. Intentional markup (mention links, the creator link) is wrapped in
   ``Raw()``, which passes through untouched.  You cannot emit a tag by
   forgetting to escape — you have to say so explicitly.
"""

from __future__ import annotations

import html
from typing import Any


class Raw(str):
    """A string that is already valid Telegram HTML and must not be escaped."""

    __slots__ = ()


def esc(value: Any) -> str:
    """Escape ``value`` unless it is explicitly marked ``Raw``."""
    if isinstance(value, Raw):
        return str(value)
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def esc_list(values: Any, sep: str = "\n") -> str:
    return sep.join(esc(v) for v in (values or []))


def user_mention(user_id: int, label: str | None = None) -> Raw:
    """A clickable mention.  Always valid HTML by construction."""
    text = label or str(user_id)
    return Raw(f'<a href="tg://user?id={int(user_id)}">{esc(text)}</a>')
