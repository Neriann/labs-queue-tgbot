import asyncio
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.fsm.strategy import FSMStrategy
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetChatAdministrators,
    GetMe,
    SendMessage,
)
from aiogram.types import CallbackQuery, Chat, ChatMemberOwner, Message, Update
from aiogram.types import User as TelegramUser

from config import Settings
from database.models import EventStatus
from database.repo import Repository
from handlers.commands import router as commands_router
from handlers.events import router as events_router
from scheduler import EventScheduler
from services.keyboards import EventAction
from services.publisher import EventPublisher

CHAT = Chat(id=-100123, type="supergroup", is_forum=True)
TOPIC = 42
BOT_USER = TelegramUser(id=123456, is_bot=True, first_name="Queue", username="queue_bot")
HOST = TelegramUser(id=1, is_bot=False, first_name="Создатель", username="event_host")
ADMIN = TelegramUser(id=2, is_bot=False, first_name="Админ")
GUEST = TelegramUser(id=3, is_bot=False, first_name="Тест & <Гость>")


class OfflineTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.edits = []
        self.answers = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError
        yield b""  # BaseSession defines this method as an async iterator.

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, SendMessage):
            message = Message(
                message_id=100 + len(self.sent),
                chat=CHAT,
                date=datetime.now(UTC),
                from_user=BOT_USER,
                text=method.text,
                is_topic_message=True,
                message_thread_id=method.message_thread_id,
            )
            self.sent.append((method, message))
            return message
        if isinstance(method, EditMessageText):
            self.edits.append(method)
            return True
        if isinstance(method, AnswerCallbackQuery):
            self.answers.append(method)
            return True
        if isinstance(method, GetChatAdministrators):
            return [ChatMemberOwner(user=ADMIN, is_anonymous=False)]
        raise AssertionError(f"Unexpected Telegram method: {method}")


async def test_fsm_topic_registration_and_admin_close(tmp_path):
    settings = Settings(
        bot_token="123456:offline-test-token",
        bot_timezone="Europe/Moscow",
        database_path=tmp_path / "queue.sqlite3",
        _env_file=None,
    )
    repo = Repository(settings.database_path)
    session = OfflineTelegram()

    async with Bot(settings.bot_token.get_secret_value(), session=session) as bot:
        publisher = EventPublisher(bot, repo, timezone=settings.timezone, fairness_power=2)
        scheduler = EventScheduler(repo, publisher, fairness_power=2, alpha=0.75)
        dispatcher = Dispatcher(
            storage=MemoryStorage(),
            events_isolation=SimpleEventIsolation(),
            fsm_strategy=FSMStrategy.USER_IN_TOPIC,
            repo=repo,
            settings=settings,
            event_scheduler=scheduler,
            publisher=publisher,
        )
        dispatcher.include_routers(commands_router, events_router)
        await scheduler.start()
        try:

            async def say(update_id, text, *, user=HOST, reply=None):
                await dispatcher.feed_update(
                    bot,
                    Update(
                        update_id=update_id,
                        message=Message(
                            message_id=update_id,
                            chat=CHAT,
                            date=datetime.now(UTC),
                            from_user=user,
                            text=text,
                            is_topic_message=True,
                            message_thread_id=TOPIC,
                            reply_to_message=reply,
                        ),
                    ),
                )

            await say(1, "/new_event@queue_bot")
            method, title_prompt = session.sent[-1]
            assert method.reply_markup.force_reply
            assert method.message_thread_id == TOPIC
            await say(2, "Свободное сообщение в группе")
            await say(3, "Ответ от другого пользователя", user=GUEST, reply=title_prompt)
            assert len(session.sent) == 1
            await say(4, "Лаба <3 & разбор", reply=title_prompt)
            _, deadline_prompt = session.sent[-1]
            await say(5, "+30m", reply=deadline_prompt)
            method, card = session.sent[-1]
            assert method.message_thread_id == TOPIC
            assert "Лаба &lt;3 &amp; разбор" in method.text
            (event,) = await repo.open_events()

            async def click(update_id, action, user):
                await dispatcher.feed_update(
                    bot,
                    Update(
                        update_id=update_id,
                        callback_query=CallbackQuery(
                            id=str(update_id),
                            from_user=user,
                            chat_instance="offline-test-chat",
                            message=card,
                            data=EventAction(action=action, event_id=event.id).pack(),
                        ),
                    ),
                )

            await click(6, "join", GUEST)
            assert session.answers[-1].text == "Вы записались."
            await click(7, "close", GUEST)
            assert session.answers[-1].show_alert
            assert (await repo.get_event(event.id)).status == EventStatus.OPEN
            await click(8, "close", ADMIN)
            assert session.answers[-1].text == "Регистрация закрыта."
            await click(9, "leave", GUEST)
            assert session.answers[-1].show_alert

            async with asyncio.timeout(5):
                while not (await repo.get_event(event.id)).result_delivered:
                    await asyncio.sleep(0.02)
            result, _ = session.sent[-1]
            assert result.message_thread_id == TOPIC
            assert '<a href="tg://user?id=3">Тест &amp; &lt;Гость&gt;</a>' in result.text
            assert session.edits[-1].reply_markup is None

            await say(10, "/my_stats", user=GUEST)
            assert "1 из 1" in session.sent[-1][0].text
            await say(11, "/stats", user=GUEST)
            assert "1.00× · 1" in session.sent[-1][0].text
        finally:
            await scheduler.stop()
            await dispatcher.fsm.close()
