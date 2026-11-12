import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from database.models import EventStatus, User
from database.repo import Repository
from scheduler import EventScheduler
from services.publisher import EventPublisher


def test_overdue_future_and_pending_results_survive_process_restart(tmp_path):
    database = tmp_path / "queue.sqlite3"
    command = [sys.executable, "-m", "tests.restart_fixture"]
    first = subprocess.run(
        [*command, "seed", str(database)], check=False, capture_output=True, text=True, timeout=20
    )
    assert first.returncode == 0, first.stderr
    ids = json.loads(first.stdout)

    async def wait_for_deadline():
        event = await Repository(database).get_event(ids[0])
        await asyncio.sleep(max(0, (event.deadline_at - datetime.now(UTC)).total_seconds()))

    asyncio.run(wait_for_deadline())
    second = subprocess.run(
        [*command, "verify", str(database), *map(str, ids)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert second.returncode == 0, second.stderr


async def test_date_trigger_closes_and_publishes_empty_event(tmp_path):
    repo = Repository(tmp_path / "queue.sqlite3")
    bot = AsyncMock()
    publisher = EventPublisher(bot, repo, timezone=ZoneInfo("UTC"), fairness_power=2)
    scheduler = EventScheduler(repo, publisher, fairness_power=2, alpha=0.75)
    await scheduler.start()
    try:
        deadline = datetime.now(UTC) + timedelta(milliseconds=200)
        event = await repo.create_event(-1001, User(1, None, "Host"), "Пустой", deadline)
        event = await repo.attach_card(event.id, 8)
        scheduler.schedule(event)
        async with asyncio.timeout(5):
            while not (await repo.get_event(event.id)).result_delivered:
                await asyncio.sleep(0.02)

        assert (await repo.get_event(event.id)).status == EventStatus.CLOSED
        assert bot.edit_message_text.await_args.kwargs["reply_markup"] is None
        assert "никто не записался" in bot.send_message.await_args.kwargs["text"]
    finally:
        await scheduler.stop()
