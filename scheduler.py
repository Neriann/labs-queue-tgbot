import logging
from datetime import UTC, datetime, timedelta
from sqlite3 import OperationalError

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database.models import Event, EventStatus
from database.repo import Repository
from services.publisher import EventPublisher

logger = logging.getLogger(__name__)


class EventScheduler:
    def __init__(
        self, repo: Repository, publisher: EventPublisher, *, fairness_power: float, alpha: float
    ) -> None:
        self.repo = repo
        self.publisher = publisher
        self.fairness_power = fairness_power
        self.alpha = alpha
        self.scheduler = AsyncIOScheduler(timezone=UTC)

    async def start(self) -> None:
        await self.repo.initialize()
        await self.repo.cancel_unpublished()
        self.scheduler.start(paused=True)
        for event in await self.repo.open_events():
            if event.deadline_at <= datetime.now(UTC):
                await self._on_deadline(event.id)
            else:
                self.schedule(event)
                self.publisher.request(event.id)

        for event in await self.repo.pending_results():
            self.publisher.request(event.id)
        self.scheduler.resume()

    async def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        await self.publisher.stop()

    def schedule(self, event: Event) -> None:
        self._add_deadline_job(event.id, event.deadline_at)

    def _add_deadline_job(self, event_id: int, run_at: datetime) -> None:
        self.scheduler.add_job(
            self._on_deadline,
            "date",
            run_date=run_at,
            args=(event_id,),
            id=str(event_id),
            misfire_grace_time=None,
            replace_existing=True,
        )

    async def close_now(self, event_id: int) -> bool:
        closed = await self._close(event_id, early=True)
        if closed:
            try:
                self.scheduler.remove_job(str(event_id))
            except JobLookupError:
                pass
        return closed

    async def _close(self, event_id: int, *, early: bool) -> bool:
        closed = await self.repo.close_event(
            event_id, fairness_power=self.fairness_power, alpha=self.alpha, early=early
        )
        if closed:
            self.publisher.request(event_id)
            try:
                for event in await self.repo.related_open_events(event_id):
                    self.publisher.request(event.id)
            except OperationalError:
                logger.exception("Cannot refresh related cards after closing event %s", event_id)
        return closed

    async def _on_deadline(self, event_id: int) -> None:
        try:
            if await self._close(event_id, early=False):
                return
            event = await self.repo.get_event(event_id)
            if event is not None and event.status == EventStatus.OPEN:
                self.schedule(event)
        except OperationalError:
            logger.exception("Cannot close event %s, retrying", event_id)
            self._add_deadline_job(event_id, datetime.now(UTC) + timedelta(seconds=3))
