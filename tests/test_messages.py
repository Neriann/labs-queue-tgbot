from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from database.models import Event, EventStatus, Participant, User
from services.messages import MESSAGE_LIMIT, card_text, result_messages, text_length


def test_large_event_cards_fit_and_result_contains_all_duplicate_names():
    event = Event(
        id=1,
        chat_id=-1001,
        creator_id=1,
        title="<Лаба> & разбор",
        deadline_at=datetime(2026, 11, 12, 15, tzinfo=UTC),
        status=EventStatus.OPEN,
        message_id=5,
        message_thread_id=None,
        card_closed=False,
        result_messages=None,
        result_parts_sent=0,
        result_delivered=False,
    )
    name = "😀 Алексей <&> " * 9
    participants = [Participant(User(number, None, name), 0.5, number) for number in range(1, 151)]
    text = card_text(event, participants, timezone=ZoneInfo("UTC"), fairness_power=2)
    assert text_length(text) <= MESSAGE_LIMIT
    assert "Записались: 150" in text
    assert "и ещё" in text

    pages = result_messages(event, participants)
    assert len(pages) > 1
    assert all(text_length(page) <= MESSAGE_LIMIT for page in pages)
    result = "\n".join(pages)
    assert " &lt;&amp;&gt; " in result
    for number in range(1, 151):
        assert f'{number}. <a href="tg://user?id={number}">' in result
        assert f"(ID {number})</a>" in result
