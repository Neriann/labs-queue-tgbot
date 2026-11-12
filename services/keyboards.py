from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class EventAction(CallbackData, prefix="event"):
    action: str
    event_id: int


def event_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Участвую",
                    callback_data=EventAction(action="join", event_id=event_id).pack(),
                ),
                InlineKeyboardButton(
                    text="❌ Отменить участие",
                    callback_data=EventAction(action="leave", event_id=event_id).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔒 Закрыть сейчас",
                    callback_data=EventAction(action="close", event_id=event_id).pack(),
                )
            ],
        ]
    )
