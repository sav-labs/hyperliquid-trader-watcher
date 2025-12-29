from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base
from sqlalchemy import text

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database is not initialized")
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            raise RuntimeError("Database is not initialized")
        return self._sessionmaker

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{self._db_path}"
        self._engine = create_async_engine(url, future=True)
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await self._ensure_schema(conn)

        logger.info("DB ready: %s", self._db_path)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    async def _ensure_schema(self, conn) -> None:
        """
        Tiny SQLite migrator: adds new columns when we evolve the schema.
        (No external migration framework required.)
        """
        cols = await conn.execute(text("PRAGMA table_info(users)"))
        existing = {row[1] for row in cols.fetchall()}

        to_add: list[str] = []
        if "alert_positions" not in existing:
            to_add.append("ALTER TABLE users ADD COLUMN alert_positions INTEGER NOT NULL DEFAULT 1")
        if "alert_liquidations" not in existing:
            to_add.append("ALTER TABLE users ADD COLUMN alert_liquidations INTEGER NOT NULL DEFAULT 1")
        if "alert_deposits" not in existing:
            to_add.append("ALTER TABLE users ADD COLUMN alert_deposits INTEGER NOT NULL DEFAULT 1")
        if "alert_withdrawals" not in existing:
            to_add.append("ALTER TABLE users ADD COLUMN alert_withdrawals INTEGER NOT NULL DEFAULT 1")

        for stmt in to_add:
            await conn.execute(text(stmt))


