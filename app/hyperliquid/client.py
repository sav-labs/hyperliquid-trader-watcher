from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from hyperliquid.info import Info

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HyperliquidUserSnapshot:
    user_state: dict[str, Any]
    account_value: str | None
    positions: dict[str, dict[str, Any]]  # coin -> position dict (normalized)


class HyperliquidClient:
    """
    hyperliquid-python-sdk is synchronous (requests). We wrap calls via asyncio.to_thread()
    so we don't block the bot's event loop.
    """

    def __init__(self) -> None:
        self._info = Info(skip_ws=True)

    async def close(self) -> None:
        # Info(skip_ws=True) doesn't start ws_manager, so nothing to close.
        return None

    async def fetch_user_state(self, address: str) -> HyperliquidUserSnapshot:
        addr = address.lower()

        def _call() -> dict[str, Any]:
            return self._info.user_state(addr)

        raw = await asyncio.to_thread(_call)
        positions: dict[str, dict[str, Any]] = {}
        for ap in raw.get("assetPositions", []) or []:
            p = ap.get("position") or {}
            coin = p.get("coin")
            if not coin:
                continue
            positions[str(coin)] = p

        account_value = None
        ms = raw.get("marginSummary") or {}
        if "accountValue" in ms:
            account_value = str(ms.get("accountValue"))

        return HyperliquidUserSnapshot(user_state=raw, account_value=account_value, positions=positions)

    async def fetch_non_funding_ledger_updates(self, address: str, start_time_ms: int, end_time_ms: int | None = None) -> list[dict[str, Any]]:
        addr = address.lower()

        def _call() -> Any:
            if end_time_ms is None:
                return self._info.user_non_funding_ledger_updates(addr, start_time_ms)
            return self._info.user_non_funding_ledger_updates(addr, start_time_ms, end_time_ms)

        data = await asyncio.to_thread(_call)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []

    async def fetch_fills_by_time(self, address: str, start_time_ms: int, end_time_ms: int | None = None) -> list[dict[str, Any]]:
        addr = address.lower()

        def _call() -> Any:
            if end_time_ms is None:
                return self._info.user_fills_by_time(addr, start_time_ms)
            return self._info.user_fills_by_time(addr, start_time_ms, end_time_ms)

        data = await asyncio.to_thread(_call)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []

    async def fetch_recent_ledger_updates(self, address: str, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent ledger updates (deposits/withdrawals) for user."""
        addr = address.lower()
        
        # Get updates from last 30 days
        import time
        end_time_ms = int(time.time() * 1000)
        start_time_ms = end_time_ms - (30 * 24 * 60 * 60 * 1000)  # 30 days ago
        
        updates = await self.fetch_non_funding_ledger_updates(addr, start_time_ms, end_time_ms)
        # Return most recent first
        return sorted(updates, key=lambda x: x.get("time", 0), reverse=True)[:limit]


