from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from services.deadlines import parse_deadline

MOSCOW = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 12, 10, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("+30m", NOW + timedelta(minutes=30)),
        ("+2h", NOW + timedelta(hours=2)),
        ("13:31", NOW + timedelta(minutes=1)),
        ("13:30", NOW + timedelta(days=1)),
        ("12.11 18:00", datetime(2026, 11, 12, 15, tzinfo=UTC)),
    ],
)
def test_supported_deadline_formats(text, expected):
    assert parse_deadline(text, MOSCOW, now=NOW) == expected


@pytest.mark.parametrize("text", ["+0m", "+4d", "24:00", "31.11 18:00", "11.11 19:00"])
def test_rejects_invalid_and_past_dated_deadlines(text):
    with pytest.raises(ValueError):
        parse_deadline(text, MOSCOW, now=NOW)


@pytest.mark.parametrize(
    ("text", "now"),
    [
        ("29.03 02:30", datetime(2026, 3, 28, tzinfo=UTC)),
        ("25.10 02:30", datetime(2026, 10, 24, tzinfo=UTC)),
    ],
)
def test_rejects_clock_changes(text, now):
    with pytest.raises(ValueError, match="перевод часов"):
        parse_deadline(text, ZoneInfo("Europe/Berlin"), now=now)
