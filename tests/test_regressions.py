"""Regression cases for configuration, verification, concurrency and recovery.

No Telegram or Cloudflare requests: external services are represented by
fakes. These cases are intentionally not executed during the static repair.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.filters import CommandObject
from aiohttp import web

from bot.callbacks.factories import JoinCb
from bot.db.models import GiveawayStatus
from bot.db.repo import (
    add_participant,
    count_participants,
    get_giveaway,
    list_winners,
    pending_notifications,
    replace_tier_winners,
)
from bot.db.turnstile_repo import consume_proof, has_valid_proof, issue_proof
from bot.handlers import participate
from bot.handlers.admin_create import build_admin_create_router
from bot.handlers.start import build_start_router
from bot.main import _configure_delivery
from bot.services import giveaway as gw_service
from bot.services import turnstile as ts
from bot.services.channels import missing_channels, validate_channels
from bot.services.notifier import retry_pending
from bot.settings import Settings
from bot.web import verify


@pytest.mark.parametrize(
    "value,expected",
    [
        ("424242", [424242]),
        ("424242, 424243", [424242, 424243]),
        ("[424242, 424243]", [424242, 424243]),
        ("", []),
    ],
)
def test_admin_ids_from_environment(monkeypatch, value, expected):
    monkeypatch.setenv("ADMIN_IDS", value)
    settings = Settings(_env_file=None)
    assert settings.admin_ids == expected


@pytest.fixture
def captcha_settings(monkeypatch):
    settings = Settings(
        _env_file=None,
        admin_ids=[],
        turnstile_enabled=True,
        turnstile_site_key='site"key',
        turnstile_secret_key="test-secret",
        public_base_url="https://giveaway.example.com",
    )
    monkeypatch.setattr(verify, "get_settings", lambda: settings)
    monkeypatch.setattr(ts, "get_settings", lambda: settings)
    monkeypatch.setattr(ts, "_bot_username", "giveaway_test_bot")
    return settings


def _request(gid, uid=1001, token="test-token"):
    return SimpleNamespace(
        match_info={"gid": str(gid)},
        query={"uid": str(uid)},
        remote="127.0.0.1",
        post=AsyncMock(return_value={"cf-turnstile-response": token}),
    )


async def test_verification_form_can_submit(sessionmaker, new_giveaway, captcha_settings):
    gid = await new_giveaway(turnstile_required=True)
    response = await verify.verify_page(_request(gid))
    assert response.content_type == "text/html"
    assert response.text.count('class="cf-turnstile"') == 1
    assert f'action="/verify/{gid}?uid=1001"' in response.text
    assert 'method="post"' in response.text
    assert 'type="submit"' in response.text
    assert 'data-action="giveaway-join"' in response.text
    assert 'data-sitekey="site&quot;key"' in response.text
    assert "__SITE_KEY__" not in response.text


async def test_verification_submit_issues_bound_proof(
    sessionmaker,
    new_giveaway,
    captcha_settings,
    monkeypatch,
):
    gid = await new_giveaway(turnstile_required=True)
    cloudflare = AsyncMock(return_value=ts.SiteverifyResult(success=True))
    monkeypatch.setattr(ts, "siteverify", cloudflare)
    response = await verify.verify_submit(_request(gid))
    assert response.status == 200
    cloudflare.assert_awaited_once_with(
        "test-token",
        expected_action="giveaway-join",
        expected_hostname="giveaway.example.com",
    )
    async with sessionmaker() as session:
        assert await has_valid_proof(session, 1001, gid)
        assert not await has_valid_proof(session, 1002, gid)
    assert f"?start=verify_{gid}_" in response.text


async def test_expired_giveaway_never_calls_cloudflare(
    sessionmaker,
    new_giveaway,
    captcha_settings,
    monkeypatch,
):
    gid = await new_giveaway(
        turnstile_required=True,
        end_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    cloudflare = AsyncMock()
    monkeypatch.setattr(ts, "siteverify", cloudflare)
    with pytest.raises(web.HTTPGone):
        await verify.verify_submit(_request(gid))
    cloudflare.assert_not_awaited()


async def test_giveaway_ending_during_cloudflare_request_cannot_issue_proof(
    sessionmaker,
    new_giveaway,
    captcha_settings,
    monkeypatch,
):
    gid = await new_giveaway(turnstile_required=True)

    async def cloudflare(*args, **kwargs):
        await gw_service.cancel(gid)
        return ts.SiteverifyResult(success=True)

    monkeypatch.setattr(ts, "siteverify", cloudflare)
    with pytest.raises(web.HTTPGone):
        await verify.verify_submit(_request(gid))
    async with sessionmaker() as session:
        assert not await has_valid_proof(session, 1001, gid)


async def test_missing_token_returns_a_retryable_form(
    sessionmaker,
    new_giveaway,
    captcha_settings,
):
    gid = await new_giveaway(turnstile_required=True)
    response = await verify.verify_submit(_request(gid, token=""))
    assert response.status == 400
    assert response.content_type == "text/html"
    assert response.text.count('class="cf-turnstile"') == 1
    assert 'method="post"' in response.text


async def test_repeated_challenges_do_not_break_proof_consumption(sessionmaker, new_giveaway):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        first = await issue_proof(session, user_id=1001, giveaway_id=gid, token="first")
        second = await issue_proof(session, user_id=1001, giveaway_id=gid, token="second")
        await session.commit()
    async with sessionmaker() as session:
        assert await consume_proof(session, user_id=1001, giveaway_id=gid)
        await session.commit()
    async with sessionmaker() as session:
        assert not await has_valid_proof(session, 1001, gid, nonce=first.nonce)
        assert await has_valid_proof(session, 1001, gid, nonce=second.nonce)


async def test_concurrent_spending_uses_a_proof_once(sessionmaker, new_giveaway):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        proof = await issue_proof(session, user_id=1001, giveaway_id=gid, token="token")
        await session.commit()

    async def spend():
        async with sessionmaker() as session:
            result = await consume_proof(session, user_id=1001, giveaway_id=gid, nonce=proof.nonce)
            await session.commit()
            return result

    assert sorted(await asyncio.gather(spend(), spend())) == [False, True]


async def test_concurrent_draws_produce_one_result(sessionmaker, new_giveaway, bot):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        await add_participant(session, giveaway_id=gid, user_id=1001, username="winner")
        await session.commit()
    await asyncio.gather(
        gw_service.finalize_and_publish(gid, bot),
        gw_service.finalize_and_publish(gid, bot),
    )
    async with sessionmaker() as session:
        assert len(await list_winners(session, gid)) == 1
    assert len([m for m in bot.sent if m[0] == 1001]) == 1


async def test_failed_draw_rolls_back_its_status_claim(
    sessionmaker,
    new_giveaway,
    bot,
    monkeypatch,
):
    gid = await new_giveaway()
    monkeypatch.setattr(
        gw_service, "log_winners", AsyncMock(side_effect=RuntimeError("disk failure"))
    )
    with pytest.raises(RuntimeError):
        await gw_service.finalize_and_publish(gid, bot)
    async with sessionmaker() as session:
        assert (await get_giveaway(session, gid)).status == GiveawayStatus.running
        assert not await list_winners(session, gid)
    assert not bot.sent


@pytest.mark.parametrize("is_member,expected", [(False, ("@required",)), (True, ())])
async def test_restricted_users_must_still_be_members(is_member, expected):
    bot = SimpleNamespace(
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(
                status="restricted",
                is_member=is_member,
            )
        )
    )
    assert await missing_channels(bot, 1001, ["@required"]) == expected


async def test_required_channel_needs_bot_admin_permissions(bot):
    bot.set_member("@required", 777000, "member")
    assert await validate_channels(bot, ["@required"])
    bot.set_member("@required", 777000, "administrator")
    assert not await validate_channels(bot, ["@required"])


async def test_failed_winner_dm_can_be_retried(sessionmaker, new_giveaway, bot):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        await add_participant(session, giveaway_id=gid, user_id=1001, username="winner")
        await session.commit()
    bot.fail_for.add(1001)
    await gw_service.finalize_and_publish(gid, bot)
    async with sessionmaker() as session:
        assert len(await pending_notifications(session, gid)) == 1
    bot.fail_for.clear()
    assert await retry_pending(bot, gid) == 1
    assert await retry_pending(bot, gid) == 0
    async with sessionmaker() as session:
        assert not await pending_notifications(session, gid)


async def test_superseded_failed_winner_is_not_retried(sessionmaker, new_giveaway, bot):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        await add_participant(session, giveaway_id=gid, user_id=1001, username="winner")
        await session.commit()
    bot.fail_for.add(1001)
    await gw_service.finalize_and_publish(gid, bot)
    async with sessionmaker() as session:
        await replace_tier_winners(session, gid, "Steam card")
        await session.commit()
    bot.fail_for.clear()
    assert await retry_pending(bot, gid) == 0


async def test_deep_link_returns_the_verified_user_to_join(
    sessionmaker,
    new_giveaway,
    captcha_settings,
):
    gid = await new_giveaway(turnstile_required=True)
    async with sessionmaker() as session:
        proof = await issue_proof(session, user_id=1001, giveaway_id=gid, token="token")
        await session.commit()
    link = ts.deep_link(gid, proof.nonce)
    payload = link.split("?start=", 1)[1]
    assert len(payload) <= 64 and re.fullmatch(r"[A-Za-z0-9_-]+", payload)
    on_start = next(
        h.callback
        for h in build_start_router().message.handlers
        if h.callback.__name__ == "on_start"
    )
    message = SimpleNamespace(from_user=SimpleNamespace(id=1001, username="u"), answer=AsyncMock())
    await on_start(message, CommandObject(command="start", args=payload))
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data.endswith(str(gid))
    async with sessionmaker() as session:
        assert await has_valid_proof(session, 1001, gid, nonce=proof.nonce)
    other = SimpleNamespace(from_user=SimpleNamespace(id=1002, username="u2"), answer=AsyncMock())
    await on_start(other, CommandObject(command="start", args=payload))
    assert "reply_markup" not in other.answer.await_args.kwargs


async def test_forwarded_channel_target_uses_current_telegram_fields(sessionmaker):
    handler = next(
        h.callback
        for h in build_admin_create_router().message.handlers
        if h.callback.__name__ == "step_target"
    )
    state = SimpleNamespace(
        update_data=AsyncMock(),
        set_state=AsyncMock(),
        get_data=AsyncMock(
            return_value={
                "end_at_iso": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "title": "Test",
                "prizes": [{"name": "Key", "count": 1}],
                "channels": [],
            }
        ),
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=424242),
        text=None,
        answer=AsyncMock(),
        forward_origin=SimpleNamespace(
            chat=SimpleNamespace(id=-100123, title="Channel", username=None)
        ),
    )
    bot = SimpleNamespace(get_chat=AsyncMock())
    await handler(message, state, bot)
    state.update_data.assert_awaited_once_with(target_chat_id=-100123, target_label="Channel")
    bot.get_chat.assert_not_awaited()


async def test_polling_clears_webhook_without_dropping_updates():
    bot = SimpleNamespace(delete_webhook=AsyncMock(), set_webhook=AsyncMock())
    settings = Settings(_env_file=None, admin_ids=[], webhook_base_url="")
    await _configure_delivery(bot, settings)
    bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
    bot.set_webhook.assert_not_awaited()


async def test_webhook_registration_logs_do_not_expose_secret(caplog):
    bot = SimpleNamespace(delete_webhook=AsyncMock(), set_webhook=AsyncMock())
    settings = Settings(
        _env_file=None,
        admin_ids=[],
        webhook_base_url="https://giveaway.example.com",
        webhook_secret="never-log-this-secret",
    )
    with caplog.at_level("INFO", logger="bot"):
        await _configure_delivery(bot, settings)
    bot.set_webhook.assert_awaited_once_with(
        url=settings.webhook_url,
        secret_token=settings.webhook_secret,
        drop_pending_updates=False,
    )
    assert settings.webhook_secret not in caplog.text
    assert settings.masked_webhook_url() in caplog.text


def _on_join():
    return next(
        h.callback
        for h in participate.build_participate_router().callback_query.handlers
        if h.callback.__name__ == "on_join"
    )


def _callback(bot):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=1001, username="joiner"),
        bot=bot,
        answer=AsyncMock(),
    )


async def test_cancel_during_channel_check_cannot_accept_an_entry(
    sessionmaker,
    new_giveaway,
    bot,
    monkeypatch,
):
    gid = await new_giveaway(required_channels=["@promo"])

    async def check_channels(*args, **kwargs):
        await gw_service.cancel(gid)
        return ()

    monkeypatch.setattr(participate, "missing_channels", check_channels)
    cq = _callback(bot)
    await _on_join()(cq, JoinCb(giveaway_id=gid))
    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 0
    assert cq.answer.await_args.kwargs["show_alert"] is True


async def test_concurrent_join_creates_one_entry(sessionmaker, new_giveaway, bot):
    gid = await new_giveaway()
    first, second = _callback(bot), _callback(bot)
    handler = _on_join()
    await asyncio.gather(
        handler(first, JoinCb(giveaway_id=gid)),
        handler(second, JoinCb(giveaway_id=gid)),
    )
    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 1
    assert sorted(
        [
            first.answer.await_args.kwargs["show_alert"],
            second.answer.await_args.kwargs["show_alert"],
        ]
    ) == [False, True]


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"success": True, "action": "giveaway-join", "hostname": "giveaway.example.com"}, True),
        ({"success": True, "hostname": "giveaway.example.com"}, False),
        ({"success": True, "action": "giveaway-join"}, False),
        ({"success": True, "action": "other", "hostname": "giveaway.example.com"}, False),
        ({"success": True, "action": "giveaway-join", "hostname": "other.example.com"}, False),
    ],
)
async def test_cloudflare_action_and_hostname_are_required(
    captcha_settings,
    monkeypatch,
    data,
    expected,
):
    response = AsyncMock()
    response.__aenter__.return_value = response
    response.json.return_value = data
    http = AsyncMock()
    http.__aenter__.return_value = http
    http.post = Mock(return_value=response)
    monkeypatch.setattr(ts.aiohttp, "ClientSession", lambda **kwargs: http)
    result = await ts.siteverify(
        "token",
        expected_action="giveaway-join",
        expected_hostname="giveaway.example.com",
    )
    assert result.success is expected
