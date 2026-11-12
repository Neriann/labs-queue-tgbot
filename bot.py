import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.fsm.strategy import FSMStrategy
from pydantic import ValidationError

from config import Settings
from database.repo import Repository
from handlers.commands import router as commands_router
from handlers.events import router as events_router
from scheduler import EventScheduler
from services.publisher import EventPublisher


async def run(settings: Settings) -> None:
    repo = Repository(settings.database_path)
    async with Bot(settings.bot_token.get_secret_value()) as bot:
        await bot.me()
        publisher = EventPublisher(
            bot, repo, timezone=settings.timezone, fairness_power=settings.fairness_power
        )
        event_scheduler = EventScheduler(
            repo, publisher, fairness_power=settings.fairness_power, alpha=settings.alpha
        )
        dispatcher = Dispatcher(
            storage=MemoryStorage(),
            events_isolation=SimpleEventIsolation(),
            fsm_strategy=FSMStrategy.USER_IN_TOPIC,
            repo=repo,
            settings=settings,
            event_scheduler=event_scheduler,
            publisher=publisher,
        )
        dispatcher.include_routers(commands_router, events_router)
        dispatcher.startup.register(event_scheduler.start)
        try:
            await dispatcher.start_polling(
                bot, allowed_updates=dispatcher.resolve_used_update_types(), close_bot_session=False
            )
        finally:
            await event_scheduler.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        settings = Settings()
    except ValidationError as exc:
        messages = [
            f"{'.'.join(map(str, error['loc'])).upper()}: {error['msg']}"
            for error in exc.errors(include_input=False, include_context=False)
        ]
        raise SystemExit("Проверьте настройки .env\n" + "\n".join(messages)) from None

    try:
        asyncio.run(run(settings))
    except TelegramUnauthorizedError:
        raise SystemExit("Telegram отклонил BOT_TOKEN. Проверьте токен в .env.") from None


if __name__ == "__main__":
    main()
