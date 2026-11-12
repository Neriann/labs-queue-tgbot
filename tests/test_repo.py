import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from database.models import EventStatus, ParticipationChange, User
from database.repo import Repository

HOST = User(1, "host", "Host")
GUEST = User(2, None, "Guest")
OTHER = User(3, None, "Other")


async def published(repo, chat_id, message_id, *, deadline=None):
    deadline = deadline or datetime.now(UTC) + timedelta(hours=1)
    event = await repo.create_event(chat_id, HOST, f"Ивент {message_id}", deadline)
    return await repo.attach_card(event.id, message_id)


async def test_parallel_close_is_atomic_and_ema_stays_in_one_chat(tmp_path):
    repo = Repository(tmp_path / "queue.sqlite3")
    await repo.initialize()
    target = await published(repo, -1001, 1)
    other_chat = await published(repo, -1002, 1)
    previous = await published(repo, -1001, 2)
    await repo.change_participation(previous.id, OTHER, join=True)
    await repo.change_participation(target.id, OTHER, join=True)
    assert await repo.change_participation(target.id, OTHER, join=False) == ParticipationChange.LEFT

    for member in (HOST, GUEST):
        assert (
            await repo.change_participation(target.id, member, join=True)
            == ParticipationChange.JOINED
        )
    assert (
        await repo.change_participation(target.id, HOST, join=True)
        == ParticipationChange.ALREADY_JOINED
    )
    await repo.change_participation(other_chat.id, HOST, join=True)

    second_repo = Repository(repo.path)
    results = await asyncio.gather(
        repo.close_event(target.id, fairness_power=2, alpha=0.75, early=True),
        second_repo.close_event(target.id, fairness_power=2, alpha=0.75, early=True),
    )
    assert sorted(results) == [False, True]
    assert (
        await repo.change_participation(target.id, HOST, join=False) == ParticipationChange.CLOSED
    )

    event, order = await repo.card_snapshot(target.id)
    assert event.status == EventStatus.CLOSED
    assert [item.assigned_position for item in order] == [1, 2]
    assert [item.debt for item in order] == [0.375, 0.625]

    stats = await repo.chat_stats(-1001)
    assert [(item.debt, item.events_count) for item in stats] == [(0.625, 1), (0.5, 0), (0.375, 1)]
    stats_in_other_chat, history = await repo.personal_stats(-1002, HOST)
    assert (stats_in_other_chat.debt, stats_in_other_chat.events_count, history) == (0.5, 0, [])
    stats, history = await repo.personal_stats(-1001, HOST)
    assert stats.events_count == 1
    assert (history[0].position, history[0].participants_count) == (
        next(item.assigned_position for item in order if item.user == HOST),
        2,
    )


async def test_single_member_increments_count_without_ema(tmp_path):
    repo = Repository(tmp_path / "queue.sqlite3")
    await repo.initialize()
    event = await published(repo, 1, 1)
    await repo.change_participation(event.id, HOST, join=True)

    assert await repo.close_event(event.id, fairness_power=2, alpha=0.75, early=True)
    stats, history = await repo.personal_stats(1, HOST)
    assert (stats.debt, stats.events_count) == (0.5, 1)
    assert [(item.position, item.participants_count) for item in history] == [(1, 1)]


async def test_past_deadline_rejects_changes_even_if_timer_has_not_run(tmp_path):
    repo = Repository(tmp_path / "queue.sqlite3")
    await repo.initialize()
    expired = await published(repo, -1001, 1, deadline=datetime.now(UTC) - timedelta(seconds=1))

    assert (
        await repo.change_participation(expired.id, HOST, join=True) == ParticipationChange.CLOSED
    )
    assert (
        await repo.change_participation(expired.id, GUEST, join=False) == ParticipationChange.CLOSED
    )
    assert (await repo.get_event(expired.id)).status == EventStatus.OPEN
    assert await repo.close_event(expired.id, fairness_power=2, alpha=0.75)
    assert await repo.chat_stats(-1001) == []


async def test_draw_failure_rolls_back_closed_status(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "queue.sqlite3")
    await repo.initialize()
    event = await published(repo, 1, 1)
    await repo.change_participation(event.id, HOST, join=True)
    await repo.change_participation(event.id, GUEST, join=True)

    def fail(*args, **kwargs):
        raise RuntimeError("draw failed")

    monkeypatch.setattr("database.repo.generate_queue", fail)
    with pytest.raises(RuntimeError, match="draw failed"):
        await repo.close_event(event.id, fairness_power=2, alpha=0.75, early=True)

    restored, participants = await repo.card_snapshot(event.id)
    assert restored.status == EventStatus.OPEN
    assert all(item.assigned_position is None for item in participants)
    assert all(item.events_count == 0 for item in await repo.chat_stats(1))
