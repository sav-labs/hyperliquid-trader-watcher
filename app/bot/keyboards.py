from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _fmt_balance(value: str | None) -> str | None:
    if not value:
        return None
    try:
        v = float(value)
        # 2 decimals is enough for UI, keep it compact
        return f"{v:,.2f}".replace(",", " ")
    except Exception:
        return value


def main_menu_kb(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Трейдеры", callback_data="menu:traders")]
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="Админ-панель", callback_data="menu:admin")])
        rows.append([InlineKeyboardButton(text="Настройки", callback_data="menu:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def traders_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить трейдеров", callback_data="traders:add")],
            [InlineKeyboardButton(text="Назад", callback_data="menu:back")],
        ]
    )


def traders_list_kb(traders: list[tuple[int, str, str | None]]) -> InlineKeyboardMarkup:
    """
    traders: [(trader_id, short_address, account_value_str)]
    Click on trader shows details.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for trader_id, short_addr, acct_val in traders:
        label = f"{short_addr}"
        bal = _fmt_balance(acct_val)
        if bal:
            label = f"{label} • ${bal}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"traders:view:{trader_id}")])
    rows.append([InlineKeyboardButton(text="Добавить", callback_data="traders:add")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def trader_detail_kb(trader_id: int, positions: list[dict] | None = None) -> InlineKeyboardMarkup:
    """
    Keyboard for trader detail card with position list.
    positions: list of dicts with keys: coin, side, unrealized_pnl, position_value
    """
    rows: list[list[InlineKeyboardButton]] = []
    
    # Add position buttons if any
    if positions:
        for pos in positions:
            coin = pos.get("coin", "???")
            side = pos.get("side", "")  # "🟢 LONG" or "🔴 SHORT"
            pnl = pos.get("unrealized_pnl", 0.0)
            pos_value = pos.get("position_value", 0.0)
            
            # Format button label: "BTC 🔴 SHORT | +$1,234 | $10,000"
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_str = f"{pnl_sign}${_fmt_balance(str(abs(pnl)))}"
            pos_val_str = f"${_fmt_balance(str(pos_value))}"
            
            label = f"{coin} {side} | {pnl_str} | {pos_val_str}"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"traders:position:{trader_id}:{coin}")])
    
    # Standard action buttons
    rows.extend([
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"traders:refresh:{trader_id}")],
        [InlineKeyboardButton(text="💰 История депозитов/выводов", callback_data=f"traders:history:{trader_id}")],
        [InlineKeyboardButton(text="🗑 Удалить трейдера", callback_data=f"traders:remove:{trader_id}")],
        [InlineKeyboardButton(text="« К списку", callback_data="traders:list")],
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def position_detail_kb(trader_id: int, coin: str) -> InlineKeyboardMarkup:
    """
    Keyboard for position detail view.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"traders:position:{trader_id}:{coin}")],
            [InlineKeyboardButton(text="« Назад к трейдеру", callback_data=f"traders:view:{trader_id}")],
        ]
    )


def admin_menu_kb(pending_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Заявки ({pending_count})", callback_data="admin:requests")],
            [InlineKeyboardButton(text="Пользователи", callback_data="admin:users")],
            [InlineKeyboardButton(text="Назад", callback_data="menu:back")],
        ]
    )


def admin_request_kb(user_tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"admin:approve:{user_tg_id}"),
                InlineKeyboardButton(text="⛔️ Отклонить", callback_data=f"admin:deny:{user_tg_id}"),
            ]
        ]
    )


def admin_user_kb(
    user_tg_id: int,
    status: str,
    alerts: tuple[bool, bool, bool, bool] = (True, True, True, True),
) -> InlineKeyboardMarkup:
    actions = []
    if status != "approved":
        actions.append(InlineKeyboardButton(text="✅ Разблок/Одобрить", callback_data=f"admin:approve:{user_tg_id}"))
    if status != "blocked":
        actions.append(InlineKeyboardButton(text="⛔️ Заблокировать", callback_data=f"admin:block:{user_tg_id}"))
    actions.append(InlineKeyboardButton(text="📣 Канал/ЛС", callback_data=f"admin:set_channel:{user_tg_id}"))

    pos, liq, dep, wd = alerts
    toggles = [
        InlineKeyboardButton(text=f"Позиции {'✅' if pos else '❌'}", callback_data=f"admin:toggle:{user_tg_id}:positions"),
        InlineKeyboardButton(
            text=f"Ликвидации {'✅' if liq else '❌'}", callback_data=f"admin:toggle:{user_tg_id}:liquidation"
        ),
    ]
    toggles2 = [
        InlineKeyboardButton(text=f"Депозиты {'✅' if dep else '❌'}", callback_data=f"admin:toggle:{user_tg_id}:deposit"),
        InlineKeyboardButton(text=f"Выводы {'✅' if wd else '❌'}", callback_data=f"admin:toggle:{user_tg_id}:withdraw"),
    ]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            actions,
            toggles,
            toggles2,
            [InlineKeyboardButton(text="Назад", callback_data="menu:admin")],
        ]
    )


