"""Exercise startup in distinct interpreter processes against the same database."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from database.models import EventStatus, User
from database.repo import Repository
from scheduler import EventScheduler
from services.publisher import EventPublisher


async def main(phase: str, path: Path) -> None:
    repo = Repository(path)
    bot = AsyncMock()
    publisher = EventPublisher(bot, repo, timezone=ZoneInfo("UTC"), fairness_power=2)
    scheduler = EventScheduler(repo, publisher, fairness_power=2, alpha=0.75)
    await scheduler.start()
    try:
        if phase == "seed":
            await seed(repo, scheduler)
        else:
            await verify(repo, scheduler, bot)
    finally:
        await scheduler.stop()


async def seed(repo: Repository, scheduler: EventScheduler) -> None:
    host = User(1, "host", "Host")
    guest = User(2, None, "Guest")
    now = datetime.now(UTC)
    overdue = await repo.create_event(-1001, host, "Скорый", now + timedelta(seconds=2))
    overdue = await repo.attach_card(overdue.id, 11)
    await repo.change_participation(overdue.id, host, join=True)
    await repo.change_participation(overdue.id, guest, join=True)
    scheduler.schedule(overdue)
    future = await repo.create_event(-1001, host, "Будущий", now + timedelta(hours=1))
    scheduler.schedule(await repo.attach_card(future.id, 12))

    pending = await repo.create_event(-1002, host, "Уже закрытый", now + timedelta(hours=1))
    pending = await repo.attach_card(pending.id, 13)
    await repo.change_participation(pending.id, host, join=True)
    await repo.close_event(pending.id, fairness_power=2, alpha=0.75, early=True)
    await repo.mark_card_closed(pending.id)
    await repo.save_result_messages(pending.id, ("Страница 1", "Страница 2"))
    await repo.advance_result(pending.id, 1, delivered=False)
    abandoned = await repo.create_event(-1002, host, "Без карточки", now + timedelta(hours=1))
    print(json.dumps([overdue.id, future.id, pending.id, abandoned.id]))


async def verify(repo: Repository, scheduler: EventScheduler, bot: AsyncMock) -> None:
    overdue_id, future_id, pending_id, abandoned_id = map(int, sys.argv[3:])
    overdue = await repo.get_event(overdue_id)
    assert overdue.status == EventStatus.CLOSED
    assert scheduler.scheduler.get_job(str(overdue_id)) is None
    assert (await repo.get_event(abandoned_id)).status == EventStatus.CANCELLED

    future = await repo.get_event(future_id)
    assert future.status == EventStatus.OPEN
    assert scheduler.scheduler.get_job(str(future_id)).next_run_time == future.deadline_at

    async with asyncio.timeout(5):
        while await repo.pending_results():
            await asyncio.sleep(0.01)
    assert (await repo.get_event(overdue_id)).result_delivered
    assert (await repo.get_event(pending_id)).result_delivered
    assert len(bot.send_message.await_args_list) == 2
    assert any(call.kwargs["text"] == "Страница 2" for call in bot.send_message.await_args_list)
    stats = await repo.chat_stats(-1001)
    assert [(item.debt, item.events_count) for item in stats] == [(0.625, 1), (0.375, 1)]
    assert (await repo.chat_stats(-1002))[0].events_count == 1


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[2])))
