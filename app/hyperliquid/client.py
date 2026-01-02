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
    perp_value: str | None     # Perp equity only
    spot_value: str | None     # Spot assets only
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
        
        # Log asset positions to find Spot balances
        asset_positions = raw.get("assetPositions", [])
        logger.debug(f"[DEBUG] assetPositions count: {len(asset_positions)}")
        
        # Log ALL asset positions to understand structure
        logger.debug(f"[DEBUG] ALL assetPositions: {json.dumps(asset_positions, indent=2)}")
        
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

        # Calculate account values
        # HyperDash shows: Total (Combined) = Perp + Spot
        account_value = None
        perp_value = None
        spot_value = None
        withdrawable = None
        
        # Get values from marginSummary (main account summary)
        ms = raw.get("marginSummary") or {}
        cross_ms = raw.get("crossMarginSummary") or {}
        
        # Perp equity from marginSummary
        perp_account_value = ms.get("accountValue")
        if perp_account_value:
            try:
                perp_value = str(float(perp_account_value))
                logger.debug(f"Perp equity (marginSummary.accountValue): {perp_value}")
            except (ValueError, TypeError):
                pass
        
        # Try to get Spot balance using multiple methods
        spot_candidates = []
        
        # Method 1: Check if there's a spotMarginSummary
        spot_ms = raw.get("spotMarginSummary") or {}
        if spot_ms.get("accountValue"):
            try:
                val = float(spot_ms["accountValue"])
                spot_candidates.append(("spotMarginSummary.accountValue", val))
                logger.debug(f"Method 1 - spotMarginSummary.accountValue: ${val:,.2f}")
            except (ValueError, TypeError):
                pass
        
        # Method 2: Calculate from assetPositions - sum spot holdings
        spot_balance_from_positions = 0.0
        for ap in raw.get("assetPositions", []):
            position = ap.get("position")
            if not position:
                continue
            
            coin = position.get("coin", "")
            szi = position.get("szi", "0")
            
            # Skip perp positions (those with non-zero szi)
            try:
                if float(szi) != 0:
                    continue
            except (ValueError, TypeError):
                continue
            
            # This might be a spot holding - look for balance fields
            for key in ["balance", "total", "accountValue", "value"]:
                if key in ap:
                    try:
                        spot_balance_from_positions += float(ap[key])
                    except (ValueError, TypeError):
                        pass
        
        if spot_balance_from_positions > 0:
            spot_candidates.append(("assetPositions (zero szi)", spot_balance_from_positions))
            logger.debug(f"Method 2 - Spot from assetPositions: ${spot_balance_from_positions:,.2f}")
        
        # Method 3: Try direct "spot" field if exists
        if "spot" in raw:
            try:
                val = float(raw["spot"])
                spot_candidates.append(("root.spot", val))
                logger.debug(f"Method 3 - root.spot: ${val:,.2f}")
            except (ValueError, TypeError):
                pass
        
        # Method 4: Try spotValue field
        if "spotValue" in raw:
            try:
                val = float(raw["spotValue"])
                spot_candidates.append(("root.spotValue", val))
                logger.debug(f"Method 4 - root.spotValue: ${val:,.2f}")
            except (ValueError, TypeError):
                pass
        
        # Method 5: Calculate as difference between totalRawUsd and perp positions
        # totalRawUsd might include isolated positions, so this is less reliable
        if perp_value and "totalRawUsd" in ms:
            try:
                total_raw = float(ms["totalRawUsd"])
                perp_val = float(perp_value)
                # If totalRawUsd is reasonable (not too large), use it
                if total_raw < perp_val * 3:  # Sanity check
                    spot_from_diff = total_raw - perp_val
                    if spot_from_diff > 0:
                        spot_candidates.append(("totalRawUsd - Perp", spot_from_diff))
                        logger.debug(f"Method 5 - totalRawUsd - Perp: ${spot_from_diff:,.2f}")
            except (ValueError, TypeError):
                pass
        
        # Choose the best candidate (prefer explicit spot fields over calculations)
        if spot_candidates:
            # Prefer spotMarginSummary, then direct fields, then calculations
            priority_order = ["spotMarginSummary", "root.spot", "root.spotValue", "totalRawUsd", "assetPositions"]
            for priority_key in priority_order:
                for name, val in spot_candidates:
                    if priority_key in name:
                        spot_value = str(val)
                        logger.info(f"Using Spot balance from {name}: ${val:,.2f}")
                        break
                if spot_value:
                    break
            
            # Fallback to first candidate
            if not spot_value:
                name, val = spot_candidates[0]
                spot_value = str(val)
                logger.info(f"Using Spot balance from {name} (fallback): ${val:,.2f}")
        
        # Calculate Total (Combined) = Perp + Spot
        if perp_value and spot_value:
            try:
                total_value = float(perp_value) + float(spot_value)
                account_value = str(total_value)
                logger.debug(f"Total (Combined): Perp {perp_value} + Spot {spot_value} = {account_value}")
            except (ValueError, TypeError):
                account_value = perp_value  # Fallback to Perp only
        elif perp_value:
            account_value = perp_value
            logger.warning("No Spot balance found, using Perp only")
        
        # Get withdrawable from root level (most accurate - includes all available funds)
        root_withdrawable = raw.get("withdrawable")
        if root_withdrawable:
            withdrawable = str(root_withdrawable)
            logger.debug(f"Using root withdrawable: {withdrawable}")
        else:
            withdrawable = str(ms.get("withdrawable", "0"))
            logger.debug(f"Using marginSummary.withdrawable: {withdrawable}")
        
        logger.debug(f"[DEBUG] Final values: account_value={account_value}, perp_value={perp_value}, spot_value={spot_value}, withdrawable={withdrawable}, total_position_value={total_position_value}")
        
        return HyperliquidUserSnapshot(
            user_state=raw,
            account_value=account_value,
            perp_value=perp_value,
            spot_value=spot_value,
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


