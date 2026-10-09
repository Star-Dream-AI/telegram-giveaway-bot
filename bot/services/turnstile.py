"""Cloudflare Turnstile — server-side verification.

The project advertised a captcha everywhere and enforced none of it: the
README documented it, ``TURNSTILE_*`` existed in ``.env.example``, every
giveaway row carried a ``turnstile_required`` flag, the public post printed
a 🛡 line — and ``add_participant()`` was called with ``verified=True``
hard-coded.  Nothing ever called Cloudflare.

This module is the missing half.  The flow is deliberately *bind the
challenge to a concrete entry, not to the browser*:

1. the user taps Join and receives ``/verify/<giveaway_id>?uid=<user_id>``;
2. the browser page renders the widget and, on POST, exchanges the client
   token for a verdict via :func:`siteverify`;
3. a single-use :class:`~bot.db.turnstile_repo.StoredProof` is stored;
4. the page offers a ``t.me`` deep link with a Join button; the proof is
   spent only when Telegram delivers the authenticated Join callback.

Storing only ``sha256(token)`` means a database dump cannot be replayed
against Cloudflare, and the proof cannot be transferred to another user
because the Join callback uses the user id supplied by Telegram itself.

Known limitation: with no bot username the deep link is unavailable and the
user must tap Join again, which is a little less safe but not exploitable
for a win-rate advantage (a human still solved the challenge).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from bot.settings import get_settings

log = logging.getLogger("bot.services.turnstile")

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_TIMEOUT = aiohttp.ClientTimeout(total=10)

_bot_username: str | None = None


def set_bot_username(username: str | None) -> None:
    """Remember the bot username so the verify page can build a deep link."""
    global _bot_username
    _bot_username = username or None


def get_bot_username() -> str | None:
    return _bot_username


@dataclass(frozen=True, slots=True)
class SiteverifyResult:
    success: bool
    error_codes: tuple[str, ...] = ()
    hostname: str | None = None
    action: str | None = None

    @property
    def detail(self) -> str:
        if self.error_codes:
            return ",".join(self.error_codes)
        return "ok" if self.success else "rejected"


async def siteverify(
    token: str,
    *,
    remote_ip: str | None = None,
    expected_action: str | None = None,
    expected_hostname: str | None = None,
) -> SiteverifyResult:
    """Exchange a client token for a verdict.  Never raises.

    Every failure mode — transport error, malformed body, explicit rejection
    — comes back as ``success=False`` so the caller has exactly one branch.
    """
    settings = get_settings()
    if not settings.turnstile_active:
        log.warning("siteverify called while Turnstile is not configured")
        return SiteverifyResult(success=False, error_codes=("misconfigured",))
    if not token:
        return SiteverifyResult(success=False, error_codes=("missing-input-response",))

    payload = {"secret": settings.turnstile_secret_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
            async with http.post(SITEVERIFY_URL, data=payload) as resp:
                data = await resp.json(content_type=None)
    except Exception as exc:  # network, TLS, JSON, timeout
        log.exception("siteverify transport failure")
        return SiteverifyResult(success=False, error_codes=(f"transport:{type(exc).__name__}",))

    if not isinstance(data, dict):
        return SiteverifyResult(success=False, error_codes=("malformed-response",))

    result = SiteverifyResult(
        success=data.get("success") is True,
        error_codes=tuple(str(c) for c in (data.get("error-codes") or [])),
        hostname=data.get("hostname"),
        action=data.get("action"),
    )
    if not result.success:
        log.info("siteverify rejected: %s", result.detail)
    if result.success and expected_action and result.action != expected_action:
        return SiteverifyResult(success=False, error_codes=("action-mismatch",))
    if result.success and expected_hostname and result.hostname != expected_hostname:
        return SiteverifyResult(success=False, error_codes=("hostname-mismatch",))
    return result


def deep_link(giveaway_id: int, nonce: str) -> str | None:
    """Return a deep link using Telegram's permitted payload characters."""
    username = _bot_username
    if not username:
        return None
    return f"https://t.me/{username}?start=verify_{int(giveaway_id)}_{nonce}"
