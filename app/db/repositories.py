from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import Select, case, delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models import DeliveryMode, Trader, TraderState, User, UserStatus, UserTrader

logger = logging.getLogger(__name__)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        res = await self._s.execute(select(User).where(User.telegram_id == telegram_id))
        return res.scalar_one_or_none()

    async def get_or_create(self, telegram_id: int, username: str | None) -> User:
        # Fast path: existing user
        user = await self.get_by_telegram_id(telegram_id)
        if user is not None:
            if username and user.username != username:
                user.username = username
            return user

        # Race-safe path: concurrent updates may insert same telegram_id.
        user = User(telegram_id=telegram_id, username=username)
        self._s.add(user)
        try:
            await self._s.flush()
            return user
        except IntegrityError:
            # Another concurrent session inserted the user first.
            await self._s.rollback()
            existing = await self.get_by_telegram_id(telegram_id)
            if existing is None:
                raise
            if username and existing.username != username:
                existing.username = username
            return existing

    async def set_admin_flag(self, telegram_id: int, is_admin: bool) -> None:
        await self._s.execute(update(User).where(User.telegram_id == telegram_id).values(is_admin=is_admin))

    async def set_status(self, telegram_id: int, status: UserStatus) -> None:
        await self._s.execute(update(User).where(User.telegram_id == telegram_id).values(status=status))

    async def list_pending(self) -> list[User]:
        res = await self._s.execute(select(User).where(User.status == UserStatus.pending).order_by(User.created_at.asc()))
        return list(res.scalars().all())

    async def list_all(self) -> list[User]:
        res = await self._s.execute(select(User).order_by(User.created_at.asc()))
        return list(res.scalars().all())

    async def set_delivery_channel(self, telegram_id: int, chat_id: str | None) -> None:
        mode = DeliveryMode.channel if chat_id else DeliveryMode.dm
        await self._s.execute(
            update(User).where(User.telegram_id == telegram_id).values(delivery_mode=mode, delivery_chat_id=chat_id)
        )

    async def toggle_alert(self, telegram_id: int, category: str) -> None:
        c = category.lower()
        field = None
        if c == "positions":
            field = User.alert_positions
        elif c == "liquidation":
            field = User.alert_liquidations
        elif c == "deposit":
            field = User.alert_deposits
        elif c == "withdraw":
            field = User.alert_withdrawals
        if field is None:
            return
        new_value = case((field.is_(True), False), else_=True)
        await self._s.execute(update(User).where(User.telegram_id == telegram_id).values({field: new_value}))


class TraderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_or_create_trader(self, address: str) -> Trader:
        addr = address.lower()
        res = await self._s.execute(select(Trader).where(Trader.address == addr))
        trader = res.scalar_one_or_none()
        if trader is not None:
            return trader
        trader = Trader(address=addr)
        self._s.add(trader)
        await self._s.flush()
        # Ensure state row exists
        state = TraderState(trader_id=trader.id)
        self._s.add(state)
        await self._s.flush()
        return trader

    async def add_trader_to_user(self, user: User, address: str) -> Trader:
        trader = await self.get_or_create_trader(address)
        # SQLite-friendly and race-safe: insert link if not exists.
        await self._s.execute(
            insert(UserTrader)
            .values(user_id=user.id, trader_id=trader.id)
            .prefix_with("OR IGNORE")
        )
        await self._s.flush()  # Ensure the link is written to DB
        return trader

    async def remove_trader_from_user(self, user: User, trader_id: int) -> None:
        await self._s.execute(delete(UserTrader).where(UserTrader.user_id == user.id, UserTrader.trader_id == trader_id))

    async def list_user_traders(self, user: User) -> list[Trader]:
        res = await self._s.execute(
            select(Trader)
            .join(UserTrader, UserTrader.trader_id == Trader.id)
            .where(UserTrader.user_id == user.id)
            .options(joinedload(Trader.state))
            .order_by(Trader.address.asc())
        )
        return list(res.scalars().all())

    async def list_distinct_traders_to_monitor(self) -> list[Trader]:
        res = await self._s.execute(
            select(Trader)
            .join(UserTrader, UserTrader.trader_id == Trader.id)
            .options(joinedload(Trader.state))
            .distinct()
        )
        return list(res.scalars().all())

    async def list_subscribers_for_trader(self, trader_id: int) -> list[User]:
        res = await self._s.execute(
            select(User)
            .join(UserTrader, UserTrader.user_id == User.id)
            .where(UserTrader.trader_id == trader_id)
            .order_by(User.telegram_id.asc())
        )
        return list(res.scalars().all())

    async def get_state(self, trader_id: int) -> TraderState:
        res = await self._s.execute(select(TraderState).where(TraderState.trader_id == trader_id))
        state = res.scalar_one_or_none()
        if state is None:
            state = TraderState(trader_id=trader_id)
            self._s.add(state)
            await self._s.flush()
        return state


