from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def _short_addr(addr: str) -> str:
    a = addr.lower()
    if len(a) <= 12:
        return a
    return f"{a[:6]}…{a[-4:]}"


def _fmt_usd(value: float) -> str:
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000_000:
        return f"{sign}${v/1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{sign}${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{sign}${v/1_000:.2f}K"
    return f"{sign}${v:.2f}"


def _to_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


@dataclass(frozen=True)
class PositionChange:
    trader_address: str
    coin: str
    old_szi: float
    new_szi: float
    leverage: int | None
    notional_usd: float | None
    realized_pnl_usd: float | None


@dataclass(frozen=True)
class LedgerEvent:
    trader_address: str
    kind: str  # deposit/withdraw/liquidation/other
    raw: dict[str, Any]


class AlertFormatter:
    def format_position_change(self, ev: PositionChange) -> str:
        who = _short_addr(ev.trader_address)
        side = "Long" if ev.new_szi > 0 else "Short"
        old_side = "Long" if ev.old_szi > 0 else "Short"

        opened = ev.old_szi == 0 and ev.new_szi != 0
        closed = ev.old_szi != 0 and ev.new_szi == 0
        flipped = ev.old_szi != 0 and ev.new_szi != 0 and (ev.old_szi > 0) != (ev.new_szi > 0)

        lev = f"{ev.leverage}x" if ev.leverage else "—"
        notional = _fmt_usd(ev.notional_usd) if ev.notional_usd is not None else "—"
        pnl = ""
        if ev.realized_pnl_usd is not None and not math.isclose(ev.realized_pnl_usd, 0.0):
            pnl = f", PnL: {_fmt_usd(ev.realized_pnl_usd)}"

        if opened:
            return f"Трейдер {who} открыл {side} позицию по {ev.coin} на {notional}, плечо {lev}{pnl}"
        if closed:
            return f"Трейдер {who} закрыл {old_side} позицию по {ev.coin}{pnl}"
        if flipped:
            return f"Трейдер {who} перевернул позицию по {ev.coin}: {old_side} → {side}, размер {notional}, плечо {lev}{pnl}"
        return f"Трейдер {who} изменил позицию по {ev.coin}: размер {notional}, плечо {lev}{pnl}"

    def format_ledger_event(self, ev: LedgerEvent) -> str:
        who = _short_addr(ev.trader_address)
        t = (ev.raw.get("type") or ev.raw.get("kind") or ev.kind) if isinstance(ev.raw, dict) else ev.kind

        # Best-effort amount extraction (API formats may vary)
        amount = ev.raw.get("delta") or ev.raw.get("amount") or ev.raw.get("usd") or ev.raw.get("usdc") or ev.raw.get("value")
        coin = ev.raw.get("coin") or ev.raw.get("token") or "USD"
        amt_str = str(amount) if amount is not None else ""

        if ev.kind == "deposit":
            return f"Трейдер {who} пополнил баланс: {amt_str} {coin}"
        if ev.kind == "withdraw":
            return f"Трейдер {who} вывел средства: {amt_str} {coin}"
        if ev.kind == "liquidation":
            return f"Трейдер {who} был ликвидирован ({t}). Детали: {ev.raw}"
        return f"Трейдер {who}: событие {t}. Детали: {ev.raw}"


