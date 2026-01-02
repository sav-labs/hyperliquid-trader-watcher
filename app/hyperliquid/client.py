from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from hyperliquid.info import Info

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HyperliquidUserSnapshot:
    user_state: dict[str, Any]
    account_value: str | None  # Total account value (Combined: Perp + Spot)
    withdrawable: str | None   # Amount available for withdrawal
    total_position_value: float  # Total notional value of all positions (for leverage calc)
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
        
        # DEBUG: Log full structure to understand API response
        logger.debug(f"[DEBUG] user_state for {addr[:10]}...")
        logger.debug(f"[DEBUG] Top-level keys: {list(raw.keys())}")
        if "marginSummary" in raw:
            logger.debug(f"[DEBUG] marginSummary: {json.dumps(raw['marginSummary'], indent=2)}")
        if "crossMarginSummary" in raw:
            logger.debug(f"[DEBUG] crossMarginSummary: {json.dumps(raw['crossMarginSummary'], indent=2)}")
        logger.debug(f"[DEBUG] withdrawable (root): {raw.get('withdrawable', 'NOT FOUND')}")
        
        positions: dict[str, dict[str, Any]] = {}
        total_position_value = 0.0
        total_spot_value = 0.0
        
        for ap in raw.get("assetPositions", []) or []:
            p = ap.get("position") or {}
            coin = p.get("coin")
            if not coin:
                continue
            positions[str(coin)] = p
            
            # Calculate total position value (notional) for Perp positions
            try:
                position_value = abs(float(p.get("positionValue", 0)))
                total_position_value += position_value
            except (ValueError, TypeError):
                pass

        # Calculate Total Value (Combined: Perp + Spot)
        account_value = None
        withdrawable = None
        perp_value = 0.0
        
        # Get Perp equity from marginSummary
        ms = raw.get("marginSummary") or {}
        if "accountValue" in ms:
            try:
                perp_value = float(ms.get("accountValue", 0))
            except (ValueError, TypeError):
                pass
        
        # Get Spot balances - look at withdrawable at root level
        # withdrawable typically includes all available funds (Perp + Spot)
        root_withdrawable = raw.get("withdrawable")
        
        # Try crossMarginSummary first (most accurate for Combined)
        cross_ms = raw.get("crossMarginSummary") or {}
        if "accountValue" in cross_ms:
            account_value = str(cross_ms.get("accountValue"))
            withdrawable = str(root_withdrawable or cross_ms.get("withdrawable", "0"))
            logger.debug(f"Using crossMarginSummary.accountValue: {account_value}")
        else:
            # Calculate total manually: Try to get total from root withdrawable + unrealized PnL
            # Or use totalRawUsd if available
            total_raw_usd = ms.get("totalRawUsd")
            if total_raw_usd:
                try:
                    account_value = str(float(total_raw_usd))
                    logger.debug(f"Using marginSummary.totalRawUsd: {account_value}")
                except (ValueError, TypeError):
                    pass
            
            # If still no account_value, fallback to perp only
            if not account_value and perp_value > 0:
                account_value = str(perp_value)
                logger.warning(f"Using marginSummary.accountValue (Perp only): {account_value}")
            
            # Use root withdrawable if available
            withdrawable = str(root_withdrawable or ms.get("withdrawable", "0"))
        
        logger.debug(f"[DEBUG] Final values: account_value={account_value}, withdrawable={withdrawable}, total_position_value={total_position_value}")

        return HyperliquidUserSnapshot(
            user_state=raw,
            account_value=account_value,
            withdrawable=withdrawable,
            total_position_value=total_position_value,
            positions=positions,
        )

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


