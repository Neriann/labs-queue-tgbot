import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from database.models import (
    SCHEMA,
    Event,
    EventStatus,
    Participant,
    ParticipationChange,
    PastPosition,
    User,
    UserStats,
)
from services.fair_queue import QueueMember, generate_queue, update_debts

Params = tuple[Any, ...]


def utc_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("Expected a timezone-aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def read_event(row: aiosqlite.Row) -> Event:
    result = row["result_messages"]
    return Event(
        id=row["id"],
        chat_id=row["chat_id"],
        creator_id=row["creator_id"],
        title=row["title"],
        deadline_at=datetime.fromisoformat(row["deadline_at"]),
        status=EventStatus(row["status"]),
        message_id=row["message_id"],
        message_thread_id=row["message_thread_id"],
        card_closed=bool(row["card_closed"]),
        result_messages=tuple(json.loads(result)) if result is not None else None,
        result_parts_sent=row["result_parts_sent"],
        result_delivered=bool(row["result_delivered"]),
    )


def read_user(row: aiosqlite.Row) -> User:
    return User(row["user_id"], row["username"], row["full_name"])


async def fetch_one(
    db: aiosqlite.Connection, sql: str, params: Params = ()
) -> aiosqlite.Row | None:
    async with db.execute(sql, params) as cursor:
        return await cursor.fetchone()


async def upsert_user(db: aiosqlite.Connection, user: User) -> None:
    await db.execute(
        """INSERT INTO users (user_id, username, full_name) VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE
           SET username = excluded.username, full_name = excluded.full_name""",
        (user.user_id, user.username, user.full_name),
    )


class Repository:
    def __init__(self, path: Path) -> None:
        self.path = path

    @asynccontextmanager
    async def _connection(
        self, *, write: bool = False, snapshot: bool = False
    ) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self.path, timeout=5.0) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            if write:
                await db.execute("BEGIN IMMEDIATE")
            elif snapshot:
                await db.execute("BEGIN")
            try:
                yield db
                if write:
                    await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connection() as db:
            async with db.execute("PRAGMA journal_mode = WAL") as cursor:
                await cursor.fetchone()
            async with db.executescript(SCHEMA):
                pass

    async def create_event(
        self,
        chat_id: int,
        creator: User,
        title: str,
        deadline_at: datetime,
        message_thread_id: int | None = None,
    ) -> Event:
        async with self._connection(write=True) as db:
            await upsert_user(db, creator)
            row = await fetch_one(
                db,
                """INSERT INTO events (chat_id, creator_id, title, deadline_at, message_thread_id)
                   VALUES (?, ?, ?, ?, ?) RETURNING *""",
                (chat_id, creator.user_id, title, utc_text(deadline_at), message_thread_id),
            )
            assert row is not None
            return read_event(row)

    async def attach_card(self, event_id: int, message_id: int) -> Event:
        async with self._connection(write=True) as db:
            row = await fetch_one(
                db,
                """UPDATE events SET message_id = ?
                   WHERE id = ? AND status = 'OPEN' AND message_id IS NULL RETURNING *""",
                (message_id, event_id),
            )
            if row is None:
                raise ValueError(f"Cannot attach a card to event {event_id}")
            return read_event(row)

    async def cancel_unpublished(self, event_id: int | None = None) -> None:
        async with self._connection(write=True) as db:
            sql = "UPDATE events SET status = 'CANCELLED' WHERE status = 'OPEN' AND message_id IS NULL"
            if event_id is None:
                await db.execute(sql)
            else:
                await db.execute(sql + " AND id = ?", (event_id,))

    async def get_event(self, event_id: int) -> Event | None:
        async with self._connection() as db:
            row = await fetch_one(db, "SELECT * FROM events WHERE id = ?", (event_id,))
        return read_event(row) if row is not None else None

    async def open_events(self) -> list[Event]:
        async with self._connection() as db:
            rows = await db.execute_fetchall(
                """SELECT * FROM events WHERE status = 'OPEN' AND message_id IS NOT NULL
                   ORDER BY deadline_at, id"""
            )
        return [read_event(row) for row in rows]

    async def related_open_events(self, closed_event_id: int) -> list[Event]:
        """Find live cards showing priorities that may have just changed."""
        async with self._connection() as db:
            rows = await db.execute_fetchall(
                """SELECT e.* FROM events e
                   WHERE e.status = 'OPEN' AND e.message_id IS NOT NULL
                     AND e.chat_id = (SELECT chat_id FROM events WHERE id = ?)
                     AND EXISTS (
                         SELECT 1 FROM event_participants current
                         JOIN event_participants previous ON previous.user_id = current.user_id
                         WHERE current.event_id = e.id AND previous.event_id = ?
                     )""",
                (closed_event_id, closed_event_id),
            )
        return [read_event(row) for row in rows]

    async def pending_results(self) -> list[Event]:
        async with self._connection() as db:
            rows = await db.execute_fetchall(
                """SELECT * FROM events WHERE status = 'CLOSED' AND result_delivered = 0
                   ORDER BY closed_at, id"""
            )
        return [read_event(row) for row in rows]

    async def change_participation(
        self, event_id: int, user: User, *, join: bool
    ) -> ParticipationChange:
        async with self._connection(write=True) as db:
            event = await fetch_one(db, "SELECT * FROM events WHERE id = ?", (event_id,))
            now = utc_text(datetime.now(UTC))
            if (
                event is None
                or event["status"] != EventStatus.OPEN
                or event["message_id"] is None
                or event["deadline_at"] <= now
            ):
                return ParticipationChange.CLOSED

            if join:
                await upsert_user(db, user)
                await db.execute(
                    """INSERT INTO user_stats (chat_id, user_id) VALUES (?, ?)
                       ON CONFLICT(chat_id, user_id) DO NOTHING""",
                    (event["chat_id"], user.user_id),
                )
                async with db.execute(
                    """INSERT INTO event_participants (event_id, user_id, joined_at)
                       VALUES (?, ?, ?) ON CONFLICT(event_id, user_id) DO NOTHING""",
                    (event_id, user.user_id, now),
                ) as cursor:
                    return (
                        ParticipationChange.JOINED
                        if cursor.rowcount
                        else ParticipationChange.ALREADY_JOINED
                    )

            async with db.execute(
                "DELETE FROM event_participants WHERE event_id = ? AND user_id = ?",
                (event_id, user.user_id),
            ) as cursor:
                return (
                    ParticipationChange.LEFT if cursor.rowcount else ParticipationChange.NOT_JOINED
                )

    async def close_event(
        self, event_id: int, *, fairness_power: float, alpha: float, early: bool = False
    ) -> bool:
        async with self._connection(write=True) as db:
            now = utc_text(datetime.now(UTC))
            event = await fetch_one(
                db,
                """UPDATE events SET status = 'CLOSED', closed_at = ?
                   WHERE id = ? AND status = 'OPEN' AND message_id IS NOT NULL
                     AND (? OR deadline_at <= ?)
                   RETURNING chat_id""",
                (now, event_id, early, now),
            )
            if event is None:
                return False

            chat_id = event["chat_id"]
            rows = await db.execute_fetchall(
                """SELECT p.user_id, s.debt
                   FROM event_participants p
                   JOIN user_stats s ON s.chat_id = ? AND s.user_id = p.user_id
                   WHERE p.event_id = ? ORDER BY p.joined_at, p.user_id""",
                (chat_id, event_id),
            )
            members = [QueueMember(row["user_id"], row["debt"]) for row in rows]
            if not members:
                return True

            order = generate_queue(members, fairness_power=fairness_power)
            debts = update_debts(order, alpha=alpha)
            async with db.executemany(
                """UPDATE event_participants SET assigned_position = ?
                   WHERE event_id = ? AND user_id = ?""",
                [(position, event_id, member.user_id) for position, member in enumerate(order, 1)],
            ):
                pass
            async with db.executemany(
                """UPDATE user_stats SET debt = ?, events_count = events_count + 1
                   WHERE chat_id = ? AND user_id = ?""",
                [(debt, chat_id, user_id) for user_id, debt in debts.items()],
            ):
                pass
            return True

    async def card_snapshot(self, event_id: int) -> tuple[Event, list[Participant]] | None:
        async with self._connection(snapshot=True) as db:
            row = await fetch_one(db, "SELECT * FROM events WHERE id = ?", (event_id,))
            if row is None:
                return None
            event = read_event(row)
            if event.status != EventStatus.OPEN and event.result_messages is not None:
                return event, []
            rows = await db.execute_fetchall(
                """SELECT u.user_id, u.username, u.full_name, s.debt, p.assigned_position
                   FROM event_participants p
                   JOIN users u ON u.user_id = p.user_id
                   JOIN user_stats s ON s.chat_id = ? AND s.user_id = u.user_id
                   WHERE p.event_id = ?
                   ORDER BY p.assigned_position, p.joined_at, p.user_id""",
                (event.chat_id, event.id),
            )
        return event, [
            Participant(read_user(row), row["debt"], row["assigned_position"]) for row in rows
        ]

    async def mark_card_closed(self, event_id: int) -> None:
        async with self._connection(write=True) as db:
            await db.execute("UPDATE events SET card_closed = 1 WHERE id = ?", (event_id,))

    async def save_result_messages(self, event_id: int, messages: tuple[str, ...]) -> None:
        async with self._connection(write=True) as db:
            await db.execute(
                "UPDATE events SET result_messages = ? WHERE id = ? AND result_messages IS NULL",
                (json.dumps(messages, ensure_ascii=False), event_id),
            )

    async def advance_result(self, event_id: int, parts_sent: int, *, delivered: bool) -> None:
        async with self._connection(write=True) as db:
            await db.execute(
                """UPDATE events SET result_parts_sent = ?, result_delivered = ?
                   WHERE id = ? AND result_parts_sent < ?""",
                (parts_sent, delivered, event_id, parts_sent),
            )

    async def chat_stats(self, chat_id: int) -> list[UserStats]:
        async with self._connection() as db:
            rows = await db.execute_fetchall(
                """SELECT u.user_id, u.username, u.full_name, s.debt, s.events_count
                   FROM user_stats s JOIN users u ON u.user_id = s.user_id
                   WHERE s.chat_id = ?
                   ORDER BY s.debt DESC, u.full_name COLLATE NOCASE, u.user_id""",
                (chat_id,),
            )
        return [UserStats(read_user(row), row["debt"], row["events_count"]) for row in rows]

    async def personal_stats(
        self, chat_id: int, user: User
    ) -> tuple[UserStats | None, list[PastPosition]]:
        async with self._connection(snapshot=True) as db:
            row = await fetch_one(
                db,
                "SELECT debt, events_count FROM user_stats WHERE chat_id = ? AND user_id = ?",
                (chat_id, user.user_id),
            )
            if row is None:
                return None, []
            positions = await db.execute_fetchall(
                """SELECT e.title, e.closed_at, p.assigned_position,
                          (SELECT COUNT(*) FROM event_participants q WHERE q.event_id = e.id)
                              AS participants_count
                   FROM events e
                   JOIN event_participants p ON p.event_id = e.id
                   WHERE e.chat_id = ? AND e.status = 'CLOSED' AND p.user_id = ?
                     AND p.assigned_position IS NOT NULL
                   ORDER BY e.closed_at DESC, e.id DESC LIMIT 5""",
                (chat_id, user.user_id),
            )
        return UserStats(user, row["debt"], row["events_count"]), [
            PastPosition(
                title=item["title"],
                closed_at=datetime.fromisoformat(item["closed_at"]),
                position=item["assigned_position"],
                participants_count=item["participants_count"],
            )
            for item in positions
        ]
