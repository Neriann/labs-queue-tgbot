import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import EditMessageText

from database.models import User
from database.repo import Repository
from services.publisher import EventPublisher


async def test_refreshes_are_coalesced_and_retry_after_is_respected(tmp_path):
    repo = Repository(tmp_path / "queue.sqlite3")
    await repo.initialize()
    bot = AsyncMock()
    publisher = EventPublisher(bot, repo, timezone=ZoneInfo("UTC"), fairness_power=2)
    now = datetime.now(UTC)
    host = User(1, None, "Хост")
    event = await repo.create_event(-1001, host, "Лаба", now + timedelta(hours=1))
    await repo.attach_card(event.id, 1)
    edits = []
    first_attempt = asyncio.Event()

    async def edit(**kwargs):
        edits.append((asyncio.get_running_loop().time(), kwargs["text"], kwargs["reply_markup"]))
        if len(edits) == 1:
            first_attempt.set()
            raise TelegramRetryAfter(
                EditMessageText(text=kwargs["text"]), "Flood control", retry_after=1
            )

    bot.edit_message_text.side_effect = edit
    try:
        publisher.request(event.id)
        await asyncio.wait_for(first_attempt.wait(), 5)
        for number in range(2, 9):
            await repo.change_participation(
                event.id, User(number, None, f"Имя {number}"), join=True
            )
            publisher.request(event.id)

        async with asyncio.timeout(5):
            while len(edits) < 2:
                await asyncio.sleep(0.02)

        assert edits[1][0] - edits[0][0] >= 1
        assert "Записались: 7" in edits[1][1]
        assert edits[1][2] is not None

        await repo.close_event(event.id, fairness_power=2, alpha=0.75, early=True)
        publisher.request(event.id)
        async with asyncio.timeout(5):
            while not (await repo.get_event(event.id)).result_delivered:
                await asyncio.sleep(0.02)

        assert len(edits) == 3
        assert edits[2][0] - edits[1][0] >= 1
        assert "Регистрация закрыта" in edits[2][1]
        assert edits[2][2] is None
    finally:
        await publisher.stop()
