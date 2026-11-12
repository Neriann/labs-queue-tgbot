from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from config import Settings
from database.repo import Repository
from handlers.events import SUPPORTED_CHATS, profile
from services.messages import personal_message, stats_messages

router = Router(name="commands")
router.message.filter(F.chat.type.in_(SUPPORTED_CHATS))

HELP = """<b>Справедливая очередь</b>

/new_event — создать ивент с дедлайном записи.
/cancel — выйти из диалога создания ивента.
/stats — приоритеты и число ивентов в этом чате.
/my_stats — ваша карма и последние пять позиций.
/help — показать команды.

Запишитесь или отмените запись кнопкой под карточкой. По дедлайну бот разыграет порядок и опубликует очередь. Создатель или администратор может закрыть запись раньше.

После ивента карма немного сдвигается в сторону вашей позиции. Первое место тянет её к нулю, последнее — к единице. Приоритет 1.00× соответствует новому участнику. Чем выше приоритет, тем больше шанс оказаться ближе к началу. Карма считается отдельно в каждом чате.

Если бот не отвечает на команду в группе, укажите его имя, например /new_event@имя_бота."""


@router.message(Command("start", "help"))
async def help_command(message: Message) -> None:
    await message.answer(HELP, parse_mode="HTML")


@router.message(Command("stats"))
async def chat_stats(message: Message, repo: Repository, settings: Settings) -> None:
    stats = await repo.chat_stats(message.chat.id)
    for page in stats_messages(stats, settings.fairness_power):
        await message.answer(page, parse_mode="HTML")


@router.message(Command("my_stats"))
async def my_stats(message: Message, repo: Repository, settings: Settings) -> None:
    if message.sender_chat or message.from_user is None:
        await message.answer("Отправьте команду от своего имени.")
        return
    stats, history = await repo.personal_stats(message.chat.id, profile(message.from_user))
    await message.answer(
        personal_message(
            stats, history, timezone=settings.timezone, fairness_power=settings.fairness_power
        ),
        parse_mode="HTML",
    )
