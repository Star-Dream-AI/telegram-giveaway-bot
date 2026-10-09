"""The join button, end to end, with no Telegram network access.

Covers the ordering rules that were previously untested and mutually
contradictory: ban, duplicate, closed giveaway, expired deadline, channel
membership, and the single-use Turnstile proof.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bot.callbacks.factories import JoinCb
from bot.db.models import GiveawayStatus
from bot.db.repo import (
    ban_user,
    count_participants,
    update_giveaway_status,
)
from bot.db.turnstile_repo import has_valid_proof, issue_proof
from bot.handlers.participate import build_participate_router


class FakeCallbackQuery:
    """Just enough CallbackQuery for on_join()."""

    def __init__(self, user_id, giveaway_id, bot, username="joiner"):
        self.from_user = type("U", (), {"id": user_id, "username": username})()
        self.bot = bot
        self.answers = []
        self.data = JoinCb(giveaway_id=giveaway_id)

    async def answer(self, text, show_alert=False):
        self.answers.append((text, show_alert))

    @property
    def last(self):
        return self.answers[-1]


def _on_join():
    """Dig the real closure out of the router, so we test production code."""
    for handler in build_participate_router().callback_query.handlers:
        if handler.callback.__name__ == "on_join":
            return handler.callback
    raise AssertionError("on_join handler is not registered")


ON_JOIN = _on_join()


async def test_join_creates_an_entry(sessionmaker, new_giveaway, bot):
    gid = await new_giveaway()
    cq = FakeCallbackQuery(1001, gid, bot)

    await ON_JOIN(cq, cq.data)

    assert cq.answers[-1][1] is False, "success is a quiet toast, not an alert"
    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 1


async def test_double_tap_reports_already_and_keeps_one_entry(
    sessionmaker, new_giveaway, bot
):
    """P1-1: the second tap must not claim success."""
    gid = await new_giveaway()

    for _ in range(2):
        cq = FakeCallbackQuery(1001, gid, bot)
        await ON_JOIN(cq, cq.data)

    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 1
    assert cq.answers[-1][1] is True, "a duplicate must not report success"


async def test_expired_giveaway_is_refused(sessionmaker, new_giveaway, bot):
    gid = await new_giveaway(
        end_at=datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    cq = FakeCallbackQuery(1001, gid, bot)

    await ON_JOIN(cq, cq.data)

    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 0
    assert cq.answers[-1][1] is True


async def test_banned_user_cannot_join(sessionmaker, new_giveaway, bot):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        await ban_user(session, 1001, reason="cheater", banned_by=1)
        await session.commit()

    cq = FakeCallbackQuery(1001, gid, bot)
    await ON_JOIN(cq, cq.data)

    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 0
    assert cq.answers[-1][1] is True


async def test_cancelled_giveaway_is_refused(sessionmaker, new_giveaway, bot):
    gid = await new_giveaway()
    async with sessionmaker() as session:
        await update_giveaway_status(session, gid, GiveawayStatus.canceled)
        await session.commit()

    cq = FakeCallbackQuery(1001, gid, bot)
    await ON_JOIN(cq, cq.data)
    assert cq.answers[-1][1] is True


async def test_required_channel_must_be_joined(sessionmaker, new_giveaway, bot):
    """P0-3: the requirement is finally enforced at join time."""
    gid = await new_giveaway(required_channels=["@promo"])

    cq = FakeCallbackQuery(1001, gid, bot)
    await ON_JOIN(cq, cq.data)

    assert "@promo" in cq.last[0], cq.last[0]
    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 0


async def test_joining_after_subscribing_succeeds(sessionmaker, new_giveaway, bot):
    gid = await new_giveaway(required_channels=["@promo"])
    bot.set_member("@promo", 1001, "member")

    cq = FakeCallbackQuery(1001, gid, bot)
    await ON_JOIN(cq, cq.data)

    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 1


async def test_channel_check_fails_closed_when_the_bot_cannot_read_it(
    sessionmaker, new_giveaway, bot
):
    """Losing access to a channel must block entry, not wave it through."""
    gid = await new_giveaway(required_channels=["@promo"])
    bot.set_member("@promo", 1001, "member")
    bot.members.pop(("@promo", 1001), None)  # simulate "chat not found"

    cq = FakeCallbackQuery(1001, gid, bot)
    await ON_JOIN(cq, cq.data)

    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 0


async def test_turnstile_blocks_without_a_proof(sessionmaker, new_giveaway, bot):
    """P0-2: the captcha is no longer a shield emoji in a template."""
    gid = await new_giveaway(turnstile_required=True)

    cq = FakeCallbackQuery(1001, gid, bot)
    await ON_JOIN(cq, cq.data)

    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 0
    assert cq.last[1] is True


async def test_a_valid_proof_admits_exactly_one_entry(
    sessionmaker, new_giveaway, bot
):
    gid = await new_giveaway(turnstile_required=True)
    async with sessionmaker() as session:
        await issue_proof(session, user_id=1001, giveaway_id=gid, token="tok")
        await session.commit()

    cq = FakeCallbackQuery(1001, gid, bot)
    await ON_JOIN(cq, cq.data)

    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 1
        # The proof was SPENT by the join, not merely checked.
        assert await has_valid_proof(session, 1001, gid) is False

    # A second tap now lands on the already-entered branch, not on verify.
    cq2 = FakeCallbackQuery(1001, gid, bot)
    await ON_JOIN(cq2, cq2.data)

    async with sessionmaker() as session:
        assert await count_participants(session, gid) == 1
