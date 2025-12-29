from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    blocked = "blocked"


class DeliveryMode(str, enum.Enum):
    dm = "dm"
    channel = "channel"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.pending, nullable=False)

    delivery_mode: Mapped[DeliveryMode] = mapped_column(Enum(DeliveryMode), default=DeliveryMode.dm, nullable=False)
    delivery_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    alert_positions: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    alert_liquidations: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    alert_deposits: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    alert_withdrawals: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    traders: Mapped[list["UserTrader"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Trader(Base):
    __tablename__ = "traders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(42), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    subscribers: Mapped[list["UserTrader"]] = relationship(back_populates="trader", cascade="all, delete-orphan")
    state: Mapped["TraderState"] = relationship(back_populates="trader", cascade="all, delete-orphan", uselist=False)


class UserTrader(Base):
    __tablename__ = "user_traders"
    __table_args__ = (UniqueConstraint("user_id", "trader_id", name="uq_user_trader"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped[User] = relationship(back_populates="traders")
    trader: Mapped[Trader] = relationship(back_populates="subscribers")


class TraderState(Base):
    __tablename__ = "trader_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id", ondelete="CASCADE"), unique=True, nullable=False)

    positions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_ledger_ts_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_fills_ts_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_account_value: Mapped[str | None] = mapped_column(String(64), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    trader: Mapped[Trader] = relationship(back_populates="state")


