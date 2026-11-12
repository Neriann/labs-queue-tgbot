from collections import Counter
from collections.abc import Iterable, Sequence
from html import escape
from zoneinfo import ZoneInfo

from database.models import Event, EventStatus, Participant, PastPosition, User, UserStats
from services.fair_queue import INITIAL_DEBT, log_priority_multiplier

# Telegram limits rendered text to 4096 characters. Counting raw UTF-16 units
# also covers emoji and leaves a conservative margin for HTML entities.
MESSAGE_LIMIT = 4000


def text_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def one_line(text: str) -> str:
    return " ".join(text.split())


def name_counts(users: Iterable[User]) -> Counter[str]:
    return Counter(one_line(user.full_name) for user in users if not user.username)


def display_name(user: User, counts: Counter[str], *, mention: bool = False) -> str:
    if user.username:
        return "@" + escape(user.username, quote=False)
    name = one_line(user.full_name)
    label = escape(name, quote=False)
    if counts[name] > 1:
        label += f" (ID {user.user_id})"
    if mention:
        return f'<a href="tg://user?id={user.user_id}">{label}</a>'
    return label


def priority(debt: float, fairness_power: float) -> str:
    exponent = log_priority_multiplier(debt, fairness_power)
    if -2 <= exponent <= 3:
        return f"{10**exponent:.2f}×"
    return f"10^({exponent:.3g})×"


def card_text(
    event: Event,
    participants: Sequence[Participant],
    *,
    timezone: ZoneInfo,
    fairness_power: float,
) -> str:
    title = escape(event.title, quote=False)
    local_deadline = event.deadline_at.astimezone(timezone)
    deadline_format = "%d.%m.%Y %H:%M:%S" if local_deadline.second else "%d.%m.%Y %H:%M"
    deadline = local_deadline.strftime(deadline_format)
    header = f"<b>{title}</b>\nДедлайн: {deadline} ({timezone.key})"
    if event.status == EventStatus.CLOSED:
        return f"{header}\n\n🔒 Регистрация закрыта."
    if not participants:
        return f"{header}\n\nПока никто не записался."

    header += f"\n\n<b>Записались: {len(participants)}</b>"
    counts = name_counts(item.user for item in participants)
    lines = [header]
    used = text_length(header)
    for index, item in enumerate(participants, 1):
        label = display_name(item.user, counts)
        line = f"{index}. {label} (приоритет: {priority(item.debt, fairness_power)})"
        used += 1 + text_length(line)
        if used > MESSAGE_LIMIT - 55:
            lines.append(f"… и ещё {len(participants) - index + 1} чел.")
            break
        lines.append(line)
    return "\n".join(lines)


def paginate(header: str, rows: Iterable[str]) -> tuple[str, ...]:
    pages = []
    lines = [header]
    used = text_length(header)
    for row in rows:
        row_size = 1 + text_length(row)
        if used + row_size > MESSAGE_LIMIT:
            pages.append("\n".join(lines))
            continuation = header + " (продолжение)"
            lines = [continuation]
            used = text_length(continuation)
        lines.append(row)
        used += row_size
    pages.append("\n".join(lines))
    return tuple(pages)


def result_messages(event: Event, participants: Sequence[Participant]) -> tuple[str, ...]:
    title = escape(event.title, quote=False)
    if not participants:
        return (f"<b>{title}</b>\nИвент не состоялся: никто не записался.",)
    counts = name_counts(item.user for item in participants)
    return paginate(
        f"<b>Очередь — {title}</b>",
        (
            f"{item.assigned_position}. {display_name(item.user, counts, mention=True)}"
            for item in participants
        ),
    )


def stats_messages(stats: Sequence[UserStats], fairness_power: float) -> tuple[str, ...]:
    if not stats:
        return ("Пока в этом чате никто не записывался на ивенты.",)
    counts = name_counts(item.user for item in stats)
    return paginate(
        "<b>Участник · Приоритет · Ивенты</b>",
        (
            f"{index}. {display_name(item.user, counts)} · "
            f"{priority(item.debt, fairness_power)} · {item.events_count}"
            for index, item in enumerate(stats, 1)
        ),
    )


def personal_message(
    stats: UserStats | None,
    history: Sequence[PastPosition],
    *,
    timezone: ZoneInfo,
    fairness_power: float,
) -> str:
    debt = stats.debt if stats else INITIAL_DEBT
    count = stats.events_count if stats else 0
    lines = [
        "<b>Ваша статистика в этом чате</b>",
        f"Долг: {debt:.3f} · Приоритет: {priority(debt, fairness_power)} · Ивенты: {count}",
        "",
    ]
    if history:
        lines.append("<b>Последние позиции</b>")
        for item in history:
            date = item.closed_at.astimezone(timezone).strftime("%d.%m")
            title = escape(item.title, quote=False)
            lines.append(f"{date} · {title} — {item.position} из {item.participants_count}")
    else:
        lines.append("Пока у вас нет позиций в очереди.")
    return "\n".join(lines)
