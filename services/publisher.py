import asyncio
import logging
from sqlite3 import OperationalError
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from database.models import Event, EventStatus, Participant
from database.repo import Repository
from services.keyboards import event_keyboard
from services.messages import card_text, result_messages

logger = logging.getLogger(__name__)


class EventPublisher:
    """One worker per live card, also responsible for its final publication."""

    def __init__(
        self, bot: Bot, repo: Repository, *, timezone: ZoneInfo, fairness_power: float
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.timezone = timezone
        self.fairness_power = fairness_power
        self._workers: dict[int, tuple[asyncio.Event, asyncio.Task[None]]] = {}

    def request(self, event_id: int) -> None:
        worker = self._workers.get(event_id)
        if worker is not None:
            worker[0].set()
            return
        pending = asyncio.Event()
        pending.set()
        task = asyncio.create_task(self._publish(event_id, pending))
        self._workers[event_id] = (pending, task)

    async def stop(self) -> None:
        tasks = [task for _, task in self._workers.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _publish(self, event_id: int, pending: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        next_edit = 0.0
        backoff = 1.0
        try:
            while True:
                await pending.wait()
                await asyncio.sleep(max(0.0, next_edit - loop.time()))
                pending.clear()
                try:
                    snapshot = await self.repo.card_snapshot(event_id)
                    if snapshot is None:
                        return
                    event, participants = snapshot
                    if event.message_id is None or event.status == EventStatus.CANCELLED:
                        return
                    if not event.card_closed:
                        next_edit = loop.time() + 1.0
                        await self._edit_card(event, participants)
                    if event.status == EventStatus.CLOSED:
                        if not event.result_delivered:
                            await self._send_result(event, participants)
                        return
                    backoff = 1.0
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after)
                    pending.set()
                except (TelegramNetworkError, TelegramServerError, OperationalError):
                    logger.warning(
                        "Temporary publication failure for event %s, retry in %.0fs",
                        event_id,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    pending.set()
        except TelegramAPIError as exc:
            logger.error("Cannot publish event %s: %s", event_id, exc)
        except Exception:
            logger.exception("Unexpected publication failure for event %s", event_id)
        finally:
            self._workers.pop(event_id, None)

    async def _edit_card(self, event: Event, participants: list[Participant]) -> None:
        is_open = event.status == EventStatus.OPEN
        try:
            await self.bot.edit_message_text(
                chat_id=event.chat_id,
                message_id=event.message_id,
                text=card_text(
                    event,
                    participants,
                    timezone=self.timezone,
                    fairness_power=self.fairness_power,
                ),
                parse_mode="HTML",
                reply_markup=event_keyboard(event.id) if is_open else None,
            )
        except (TelegramRetryAfter, TelegramNetworkError, TelegramServerError):
            raise
        except TelegramAPIError as exc:
            unchanged = isinstance(exc, TelegramBadRequest) and "not modified" in str(exc).lower()
            if not unchanged:
                logger.warning("Cannot edit card for event %s: %s", event.id, exc)
        if not is_open:
            await self.repo.mark_card_closed(event.id)

    async def _send_result(self, event: Event, participants: list[Participant]) -> None:
        messages = event.result_messages
        if messages is None:
            messages = result_messages(event, participants)
            await self.repo.save_result_messages(event.id, messages)

        for index in range(event.result_parts_sent, len(messages)):
            await self.bot.send_message(
                chat_id=event.chat_id,
                message_thread_id=event.message_thread_id,
                text=messages[index],
                parse_mode="HTML",
            )
            parts_sent = index + 1
            await self.repo.advance_result(
                event.id, parts_sent, delivered=parts_sent == len(messages)
            )
