from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class EventStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ParticipationChange(StrEnum):
    JOINED = "joined"
    ALREADY_JOINED = "already_joined"
    LEFT = "left"
    NOT_JOINED = "not_joined"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class User:
    user_id: int
    username: str | None
    full_name: str


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    chat_id: int
    creator_id: int
    title: str
    deadline_at: datetime
    status: EventStatus
    message_id: int | None
    message_thread_id: int | None
    card_closed: bool
    result_messages: tuple[str, ...] | None
    result_parts_sent: int
    result_delivered: bool


@dataclass(frozen=True, slots=True)
class Participant:
    user: User
    debt: float
    assigned_position: int | None


@dataclass(frozen=True, slots=True)
class UserStats:
    user: User
    debt: float
    events_count: int


@dataclass(frozen=True, slots=True)
class PastPosition:
    title: str
    closed_at: datetime
    position: int
    participants_count: int


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_stats (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    debt REAL NOT NULL DEFAULT 0.5 CHECK (debt BETWEEN 0.0 AND 1.0),
    events_count INTEGER NOT NULL DEFAULT 0 CHECK (events_count >= 0),
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    creator_id INTEGER NOT NULL REFERENCES users(user_id),
    title TEXT NOT NULL,
    deadline_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'CANCELLED')),
    message_id INTEGER,
    message_thread_id INTEGER,
    closed_at TEXT,
    card_closed INTEGER NOT NULL DEFAULT 0,
    result_messages TEXT,
    result_parts_sent INTEGER NOT NULL DEFAULT 0,
    result_delivered INTEGER NOT NULL DEFAULT 0,
    UNIQUE (chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS event_participants (
    event_id INTEGER NOT NULL REFERENCES events(id),
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    joined_at TEXT NOT NULL,
    assigned_position INTEGER CHECK (assigned_position > 0),
    PRIMARY KEY (event_id, user_id),
    UNIQUE (event_id, assigned_position)
);

CREATE INDEX IF NOT EXISTS open_events_by_deadline
    ON events (deadline_at, id) WHERE status = 'OPEN';
CREATE INDEX IF NOT EXISTS closed_events_by_chat
    ON events (chat_id, closed_at, id) WHERE status = 'CLOSED';
CREATE INDEX IF NOT EXISTS participants_by_user
    ON event_participants (user_id, event_id);
"""
