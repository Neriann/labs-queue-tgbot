import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ForceReply, Message
from aiogram.types import User as TelegramUser

from config import Settings
from database.models import EventStatus, ParticipationChange, User
from database.repo import Repository
from scheduler import EventScheduler
from services.deadlines import DEADLINE_FORMATS, parse_deadline
from services.keyboards import EventAction
from services.messages import card_text, one_line
from services.publisher import EventPublisher

logger = logging.getLogger(__name__)
router = Router(name="events")
SUPPORTED_CHATS = (ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP)
TITLE_LIMIT = 120
CLOSED_ALERT = "Регистрация на этот ивент уже закрыта"

router.message.filter(F.chat.type.in_(SUPPORTED_CHATS))


class NewEvent(StatesGroup):
    title = State()
    deadline = State()


def profile(user: TelegramUser) -> User:
    return User(user_id=user.id, username=user.username, full_name=user.full_name)


async def ask(message: Message, state: FSMContext, text: str) -> None:
    prompt = await message.reply(text, reply_markup=ForceReply(selective=True))
    await state.update_data(prompt_id=prompt.message_id)


async def is_prompt_reply(message: Message, state: FSMContext) -> bool:
    if message.chat.type == ChatType.PRIVATE:
        return True
    if message.reply_to_message is None:
        return False
    data = await state.get_data()
    return message.reply_to_message.message_id == data.get("prompt_id")


@router.message(Command("new_event"))
async def new_event(message: Message, state: FSMContext) -> None:
    if message.sender_chat or message.from_user is None:
        await message.answer("Отправьте команду от своего имени.")
        return
    await state.clear()
    await state.set_state(NewEvent.title)
    await ask(message, state, "Как называется ивент? Ответьте на это сообщение. /cancel — выйти.")


@router.message(Command("cancel"), StateFilter(NewEvent))
async def cancel_dialog(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Создание ивента отменено.")


@router.message(NewEvent.title, F.text, ~F.text.startswith("/"))
async def event_title(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await is_prompt_reply(message, state):
        return
    title = one_line(message.text)
    if not title or len(title) > TITLE_LIMIT:
        await ask(message, state, f"Введите название от 1 до {TITLE_LIMIT} символов.")
        return
    await state.update_data(title=title)
    await state.set_state(NewEvent.deadline)
    await ask(
        message,
        state,
        f"Когда закрыть запись? Форматы: {DEADLINE_FORMATS}.\n"
        f"Часовой пояс: {settings.bot_timezone}. Ответьте на это сообщение.",
    )


@router.message(NewEvent.deadline, F.text, ~F.text.startswith("/"))
async def event_deadline(
    message: Message,
    state: FSMContext,
    bot: Bot,
    repo: Repository,
    settings: Settings,
    event_scheduler: EventScheduler,
    publisher: EventPublisher,
) -> None:
    if not await is_prompt_reply(message, state) or message.from_user is None:
        return
    try:
        deadline = parse_deadline(message.text, settings.timezone)
    except ValueError as exc:
        await ask(message, state, str(exc))
        return

    data = await state.get_data()
    thread_id = message.message_thread_id if message.is_topic_message else None
    event = await repo.create_event(
        message.chat.id, profile(message.from_user), data["title"], deadline, thread_id
    )
    try:
        card = await bot.send_message(
            chat_id=event.chat_id,
            message_thread_id=event.message_thread_id,
            text=card_text(
                event, [], timezone=settings.timezone, fairness_power=settings.fairness_power
            ),
            parse_mode="HTML",
        )
    except TelegramAPIError:
        await repo.cancel_unpublished(event.id)
        logger.exception("Cannot create card for event %s", event.id)
        await ask(message, state, "Карточка не отправилась. Укажите дедлайн ещё раз.")
        return

    event = await repo.attach_card(event.id, card.message_id)
    event_scheduler.schedule(event)
    publisher.request(event.id)
    await state.clear()
    if settings.pin_event_messages:
        try:
            await bot.pin_chat_message(event.chat_id, card.message_id, disable_notification=True)
        except TelegramAPIError as exc:
            logger.warning("Cannot pin card for event %s: %s", event.id, exc)
            await message.answer("Ивент создан. Закрепить карточку не удалось.")


@router.message(StateFilter(NewEvent), ~F.text)
async def request_text(message: Message, state: FSMContext) -> None:
    if await is_prompt_reply(message, state):
        await ask(message, state, "Ответьте текстом, пожалуйста.")


@router.callback_query(EventAction.filter(F.action.in_({"join", "leave", "close"})))
async def event_action(
    callback: CallbackQuery,
    callback_data: EventAction,
    bot: Bot,
    repo: Repository,
    event_scheduler: EventScheduler,
    publisher: EventPublisher,
) -> None:
    event = await repo.get_event(callback_data.event_id)
    origin = callback.message
    if (
        event is None
        or origin is None
        or event.chat_id != origin.chat.id
        or event.message_id != origin.message_id
    ):
        await callback.answer("Карточка недоступна.", show_alert=True)
        return
    if event.status != EventStatus.OPEN:
        await callback.answer(CLOSED_ALERT, show_alert=True)
        return

    if callback_data.action == "close":
        await close_callback(
            callback, bot, event.chat_id, event.creator_id, event.id, event_scheduler
        )
        return

    change = await repo.change_participation(
        event.id, profile(callback.from_user), join=callback_data.action == "join"
    )
    if change == ParticipationChange.CLOSED:
        await callback.answer(CLOSED_ALERT, show_alert=True)
        return
    if change != ParticipationChange.NOT_JOINED:
        publisher.request(event.id)
    responses = {
        ParticipationChange.JOINED: "Вы записались.",
        ParticipationChange.ALREADY_JOINED: "Вы уже записаны.",
        ParticipationChange.LEFT: "Запись отменена.",
        ParticipationChange.NOT_JOINED: "Вы ещё не записывались.",
    }
    await callback.answer(responses[change])


async def close_callback(
    callback: CallbackQuery,
    bot: Bot,
    chat_id: int,
    creator_id: int,
    event_id: int,
    event_scheduler: EventScheduler,
) -> None:
    if callback.from_user.id != creator_id:
        if callback.message.chat.type == ChatType.PRIVATE:
            await callback.answer("Закрыть ивент может его создатель.", show_alert=True)
            return
        try:
            admins = await bot.get_chat_administrators(chat_id)
        except TelegramAPIError:
            logger.exception("Cannot check administrators of chat %s", chat_id)
            await callback.answer("Не удалось проверить права администратора.", show_alert=True)
            return
        if not any(admin.user.id == callback.from_user.id for admin in admins):
            await callback.answer(
                "Закрыть ивент может его создатель или администратор чата.", show_alert=True
            )
            return

    closed = await event_scheduler.close_now(event_id)
    if closed:
        await callback.answer("Регистрация закрыта.")
    else:
        await callback.answer(CLOSED_ALERT, show_alert=True)
