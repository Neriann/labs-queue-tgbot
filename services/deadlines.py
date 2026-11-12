import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

RELATIVE = re.compile(r"\+([0-9]{1,9})([mh])", re.IGNORECASE)
ABSOLUTE = re.compile(r"(?:([0-9]{2})\.([0-9]{2}) +)?([0-9]{2}):([0-9]{2})")
DEADLINE_FORMATS = "+30m, +2h, ДД.ММ ЧЧ:ММ или ЧЧ:ММ"


def parse_deadline(text: str, timezone: ZoneInfo, *, now: datetime | None = None) -> datetime:
    """Parse input and return an aware UTC datetime."""
    now = datetime.now(UTC) if now is None else now.astimezone(UTC)
    text = text.strip()
    relative = RELATIVE.fullmatch(text)
    if relative:
        value, unit = relative.groups()
        amount = int(value)
        if amount == 0:
            raise ValueError("Интервал должен быть больше нуля.")
        minutes = amount * (60 if unit.lower() == "h" else 1)
        try:
            return now + timedelta(minutes=minutes)
        except OverflowError as exc:
            raise ValueError("Слишком большой интервал.") from exc

    absolute = ABSOLUTE.fullmatch(text)
    if not absolute:
        raise ValueError(f"Введите время в одном из форматов: {DEADLINE_FORMATS}.")

    day, month, hour, minute = absolute.groups()
    local_now = now.astimezone(timezone).replace(tzinfo=None)
    try:
        local = local_now.replace(
            day=int(day) if day else local_now.day,
            month=int(month) if month else local_now.month,
            hour=int(hour),
            minute=int(minute),
            second=0,
            microsecond=0,
        )
        if day is None and local <= local_now:
            local += timedelta(days=1)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Такой даты или времени нет.") from exc

    # During a clock change, a wall time can map to zero or two UTC instants.
    candidates = set()
    for fold in (0, 1):
        utc = local.replace(tzinfo=timezone, fold=fold).astimezone(UTC)
        if utc.astimezone(timezone).replace(tzinfo=None) == local:
            candidates.add(utc)
    if len(candidates) != 1:
        raise ValueError("Это время попадает на перевод часов. Укажите интервал.")
    deadline = candidates.pop()
    if deadline <= now:
        raise ValueError("Дедлайн должен быть в будущем.")
    return deadline
