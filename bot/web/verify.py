"""Turnstile form, server-side validation, and return-to-bot links."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse

from aiohttp import web

from bot.db.base import get_sessionmaker
from bot.db.models import GiveawayStatus
from bot.db.repo import claim_giveaway, get_giveaway, is_blacklisted
from bot.db.turnstile_repo import issue_proof
from bot.i18n import t
from bot.services import turnstile as ts
from bot.settings import get_settings

log = logging.getLogger("bot.web.verify")

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
__SCRIPT__
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.6 -apple-system, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 24px; max-width: 560px; margin-inline: auto; }
  h1 { font-size: 20px; margin: 0 0 12px; }
  p { margin: 8px 0; }
  .ok { color: #1a7f37; }
  .err { color: #b42318; }
  .btn { display: inline-block; margin-top: 12px; padding: 10px 18px;
         background: #2AABEE; color: #fff; border-radius: 8px;
         text-decoration: none; font-weight: 600; border: 0; cursor: pointer; }
</style>
</head>
<body><h1>__TITLE__</h1>__BODY__</body>
</html>
"""

_WIDGET_SCRIPT = (
    '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>'
)


def _page(*, title: str, body: str, with_widget: bool = False) -> str:
    return (
        _PAGE.replace("__TITLE__", escape(title))
        .replace("__SCRIPT__", _WIDGET_SCRIPT if with_widget else "")
        .replace("__BODY__", body)
    )


def _response(body: str, *, with_widget: bool = False, status: int = 200) -> web.Response:
    return web.Response(
        text=_page(title=t("button.verify"), body=body, with_widget=with_widget),
        status=status,
        content_type="text/html",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


def _form(gid: int, uid: int) -> str:
    settings = get_settings()
    return (
        "<p>" + escape(t("join.need_verify_prompt")) + "</p>"
        f'<form method="post" action="/verify/{gid}?uid={uid}">'
        '<div class="cf-turnstile" data-action="giveaway-join" data-sitekey="'
        + escape(settings.turnstile_site_key, quote=True)
        + '"></div>'
        '<button class="btn" type="submit">' + escape(t("button.verify")) + "</button></form>"
    )


def _ok_body(link: str, label: str) -> str:
    return (
        '<p class="ok">' + escape(t("join.verify_ok")) + "</p>"
        "<p>" + escape(t("join.verify_return")) + "</p>"
        '<p><a class="btn" href="' + escape(link, quote=True) + '">' + escape(label) + "</a></p>"
    )


async def _context(request: web.Request) -> tuple[int, int]:
    if not get_settings().turnstile_capable():
        raise web.HTTPServiceUnavailable(text="Verification unavailable")
    try:
        gid = int(request.match_info["gid"])
        uid = int(request.query.get("uid") or request.query.get("user_id") or "")
        if not (0 < gid <= 2**63 - 1 and 0 < uid <= 2**63 - 1):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="Invalid giveaway or user id") from None
    async with get_sessionmaker()() as session:
        gw = await get_giveaway(session, gid)
        if gw is None:
            raise web.HTTPNotFound(text="Giveaway not found")
        if (
            gw.status != GiveawayStatus.running
            or gw.end_at <= datetime.now(timezone.utc)
            or not gw.turnstile_required
        ):
            raise web.HTTPGone(text="Verification is no longer required or available")
        if await is_blacklisted(session, uid):
            raise web.HTTPForbidden(text="Entry is not allowed")
    return gid, uid


async def verify_page(request: web.Request) -> web.Response:
    gid, uid = await _context(request)
    return _response(_form(gid, uid), with_widget=True)


async def verify_submit(request: web.Request) -> web.Response:
    gid, uid = await _context(request)
    form = await request.post()
    token = str(form.get("cf-turnstile-response") or "")
    if not token:
        return _response(
            '<p class="err">' + escape(t("join.verify_no_token")) + "</p>" + _form(gid, uid),
            with_widget=True,
            status=400,
        )
    result = await ts.siteverify(
        token,
        expected_action="giveaway-join",
        expected_hostname=urlparse(get_settings().public_origin).hostname,
    )
    if not result.success:
        log.info("captcha rejected for %d: %s", uid, result.detail)
        return _response(
            '<p class="err">' + escape(t("join.verify_failed")) + "</p>" + _form(gid, uid),
            with_widget=True,
            status=403,
        )
    async with get_sessionmaker()() as session:
        # The giveaway may have ended during Cloudflare's request.
        gw = await claim_giveaway(session, gid, GiveawayStatus.running, require_open=True)
        if gw is None or not gw.turnstile_required:
            raise web.HTTPGone(text="Giveaway has ended")
        if await is_blacklisted(session, uid):
            raise web.HTTPForbidden(text="Entry is not allowed")
        proof = await issue_proof(
            session,
            user_id=uid,
            giveaway_id=gid,
            token=token,
            remote_ip=request.remote,
        )
        await session.commit()
    link = ts.deep_link(gid, proof.nonce)
    if link:
        body = _ok_body(link, t("button.open_bot"))
    else:
        body = (
            '<p class="ok">' + escape(t("join.verify_ok")) + "</p>"
            "<p>" + escape(t("join.verify_return_no_link")) + "</p>"
        )
    return _response(body)


async def verify_done(request: web.Request) -> web.Response:
    try:
        gid = int(request.match_info["gid"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="Invalid giveaway id") from None
    nonce = request.query.get("nonce") or ""
    if not nonce or len(nonce) != 32 or not nonce.replace("-", "").replace("_", "").isalnum():
        raise web.HTTPBadRequest(text="Invalid verification link")
    link = ts.deep_link(gid, nonce)
    if link:
        return _response(_ok_body(link, t("button.open_bot")))
    return _response('<p class="err">' + escape(t("join.verify_expired")) + "</p>")


def register_verify_routes(app: web.Application) -> None:
    app.router.add_post("/verify/{gid}", verify_submit)
    app.router.add_get("/verify/{gid}", verify_page)
    app.router.add_get("/done/{gid}", verify_done)
    log.info("verification routes registered: /verify/{gid}, /done/{gid}")
