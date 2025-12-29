from __future__ import annotations

import logging

from aiogram import Bot

from app.db.engine import Database
from app.db.models import DeliveryMode, UserStatus
from app.db.repositories import TraderRepository

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot: Bot, db: Database) -> None:
        self._bot = bot
        self._db = db

    async def notify_trader_subscribers(self, trader_id: int, text: str, category: str) -> None:
        async with self._db.sessionmaker() as session:
            repo = TraderRepository(session)
            users = await repo.list_subscribers_for_trader(trader_id)

            for u in users:
                if u.status != UserStatus.approved:
                    continue
                if not self._is_allowed(u, category):
                    continue

                chat_id: int | str
                if u.delivery_mode == DeliveryMode.channel and u.delivery_chat_id:
                    chat_id = u.delivery_chat_id
                else:
                    chat_id = u.telegram_id

                try:
                    await self._bot.send_message(chat_id=chat_id, text=text)
                except Exception:
                    logger.exception("Failed to send message to %s (mode=%s)", chat_id, u.delivery_mode)

    @staticmethod
    def _is_allowed(user, category: str) -> bool:
        c = category.lower()
        if c == "positions":
            return bool(getattr(user, "alert_positions", True))
        if c == "liquidation":
            return bool(getattr(user, "alert_liquidations", True))
        if c == "deposit":
            return bool(getattr(user, "alert_deposits", True))
        if c == "withdraw":
            return bool(getattr(user, "alert_withdrawals", True))
        return True


