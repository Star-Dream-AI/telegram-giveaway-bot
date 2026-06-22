from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from bot.db.base import get_sessionmaker
from bot.db.repo import list_running_giveaways

log = logging.getLogger("bot.services.scheduler")


class GiveawayScheduler:
    def __init__(self, bot: Bot, tz: str = "UTC") -> None:
        self._bot = bot
        self._tz = tz
        self._scheduler = AsyncIOScheduler(timezone=tz)

    def start(self) -> None:
        self._scheduler.start()
        log.info("scheduler started (tz=%s)", self._tz)

    def shutdown(self) -> None:
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            log.exception("scheduler shutdown failed")

    async def restore_from_db(self) -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            running = await list_running_giveaways(session)
        log.info("restoring %d running giveaway(s)", len(running))
        for gw in running:
            self.schedule(gw.id, gw.end_at)

    def schedule(self, giveaway_id: int, end_at: datetime) -> None:
        from bot.services import giveaway as gw_service

        now = datetime.now(timezone.utc)
        run_at = max(end_at, now)
        job_id = f"giveaway-{giveaway_id}"
        self._scheduler.add_job(
            gw_service.finalize_and_publish,
            trigger=DateTrigger(run_date=run_at, timezone=self._tz),
            id=job_id,
            replace_existing=True,
            kwargs={"giveaway_id": giveaway_id, "bot": self._bot},
        )
        log.info("scheduled %s at %s", job_id, run_at.isoformat())

    def cancel(self, giveaway_id: int) -> None:
        job_id = f"giveaway-{giveaway_id}"
        try:
            self._scheduler.remove_job(job_id)
            log.info("canceled %s", job_id)
        except Exception:
            log.debug("no job to cancel: %s", job_id)
