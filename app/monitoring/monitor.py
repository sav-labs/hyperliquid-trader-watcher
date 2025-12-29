from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.db.engine import Database
from app.db.repositories import TraderRepository
from app.hyperliquid.client import HyperliquidClient
from app.notify.formatter import AlertFormatter, LedgerEvent, PositionChange
from app.notify.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _extract_leverage(position: dict[str, Any]) -> int | None:
    lev = position.get("leverage") or {}
    try:
        return int(lev.get("value"))
    except Exception:
        return None


def _extract_notional_usd(position: dict[str, Any]) -> float | None:
    v = position.get("positionValue")
    try:
        return abs(float(v))
    except Exception:
        return None


class TraderMonitor:
    def __init__(
        self,
        db: Database,
        hl: HyperliquidClient,
        notifier: TelegramNotifier,
        formatter: AlertFormatter,
        poll_interval_seconds: int,
    ) -> None:
        self._db = db
        self._hl = hl
        self._notifier = notifier
        self._formatter = formatter
        self._poll_interval_seconds = poll_interval_seconds
        self._sem = asyncio.Semaphore(8)

    async def run_forever(self) -> None:
        logger.info("Hyperliquid monitor started (interval=%ss)", self._poll_interval_seconds)
        while True:
            started = time.time()
            try:
                await self._poll_once()
            except Exception:
                logger.exception("Monitor tick failed")

            elapsed = time.time() - started
            sleep_for = max(0.1, self._poll_interval_seconds - elapsed)
            await asyncio.sleep(sleep_for)

    async def _poll_once(self) -> None:
        async with self._db.sessionmaker() as session:
            repo = TraderRepository(session)
            traders = await repo.list_distinct_traders_to_monitor()

        if not traders:
            return

        async def _task(trader_id: int, address: str) -> None:
            async with self._sem:
                await self._poll_trader(trader_id=trader_id, address=address)

        await asyncio.gather(*[_task(t.id, t.address) for t in traders])

    async def _poll_trader(self, trader_id: int, address: str) -> None:
        async with self._db.sessionmaker() as session:
            repo = TraderRepository(session)
            state = await repo.get_state(trader_id)

            # Bootstrap: do not spam on first run for this trader.
            is_bootstrap = state.positions_json is None and state.last_ledger_ts_ms is None and state.last_fills_ts_ms is None

            snapshot = await self._hl.fetch_user_state(address)
            now_ms = _now_ms()

            # Positions diff
            old_positions: dict[str, Any] = {}
            if state.positions_json:
                try:
                    old_positions = json.loads(state.positions_json) or {}
                except Exception:
                    logger.warning("Bad positions_json for trader_id=%s", trader_id)

            new_positions = snapshot.positions

            # Fetch fills window for realized pnl best-effort
            fills_by_coin: dict[str, float] = {}
            fills_start = state.last_fills_ts_ms or (now_ms - 5 * 60 * 1000)
            fills = []
            try:
                fills = await self._hl.fetch_fills_by_time(address, fills_start, now_ms)
            except Exception:
                logger.debug("fills_by_time failed for %s", address, exc_info=True)

            for f in fills:
                coin = f.get("coin")
                if not coin:
                    continue
                pnl = _safe_float(f.get("closedPnl"))
                if pnl == 0:
                    continue
                fills_by_coin[str(coin)] = fills_by_coin.get(str(coin), 0.0) + pnl

            position_events: list[PositionChange] = []
            all_coins = set(old_positions.keys()) | set(new_positions.keys())
            for coin in sorted(all_coins):
                op = old_positions.get(coin) or {}
                np = new_positions.get(coin) or {}
                old_szi = _safe_float(op.get("szi"))
                new_szi = _safe_float(np.get("szi"))
                if old_szi == new_szi:
                    continue

                position_events.append(
                    PositionChange(
                        trader_address=address,
                        coin=coin,
                        old_szi=old_szi,
                        new_szi=new_szi,
                        leverage=_extract_leverage(np) or _extract_leverage(op),
                        notional_usd=_extract_notional_usd(np) or _extract_notional_usd(op),
                        realized_pnl_usd=fills_by_coin.get(coin),
                    )
                )

            # Ledger updates for deposits/withdrawals/liquidations
            ledger_events: list[LedgerEvent] = []
            ledger_start = state.last_ledger_ts_ms or (now_ms - 5 * 60 * 1000)
            try:
                updates = await self._hl.fetch_non_funding_ledger_updates(address, ledger_start, now_ms)
            except Exception:
                updates = []
                logger.debug("ledger_updates failed for %s", address, exc_info=True)

            for u in updates:
                kind = self._classify_ledger_event(u)
                if kind in {"deposit", "withdraw", "liquidation"}:
                    ledger_events.append(LedgerEvent(trader_address=address, kind=kind, raw=u))

            # Persist state
            state.positions_json = json.dumps(new_positions, ensure_ascii=False)
            state.last_ledger_ts_ms = now_ms
            state.last_fills_ts_ms = now_ms
            state.last_account_value = snapshot.account_value
            await session.commit()

        # Send alerts outside transaction
        if is_bootstrap:
            return

        for ev in position_events:
            await self._notifier.notify_trader_subscribers(
                trader_id, self._formatter.format_position_change(ev), category="positions"
            )

        for ev in ledger_events:
            await self._notifier.notify_trader_subscribers(
                trader_id, self._formatter.format_ledger_event(ev), category=ev.kind
            )

    @staticmethod
    def _classify_ledger_event(u: dict[str, Any]) -> str:
        t = str(u.get("type") or u.get("kind") or "").lower()

        # We keep this flexible: Hyperliquid's event naming can evolve.
        if any(x in t for x in ["deposit", "bridgedeposit", "usdcdeposit"]):
            return "deposit"
        if any(x in t for x in ["withdraw", "bridgewithdraw", "usdcwithdraw"]):
            return "withdraw"
        if "liquid" in t:
            return "liquidation"

        # Ignore noisy bookkeeping by default
        return "ignore"


