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
        [
            InlineKeyboardButton(text="Трейдеры", callback_data="menu:traders"),
            InlineKeyboardButton(text="Настройки", callback_data="menu:settings"),
        ]
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="Админ-панель", callback_data="menu:admin")])
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
    Remove uses inline button (as requested).
    """
    rows: list[list[InlineKeyboardButton]] = []
    for trader_id, short_addr, acct_val in traders:
        label = f"{short_addr}"
        bal = _fmt_balance(acct_val)
        if bal:
            label = f"{label} • ${bal}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"traders:remove:{trader_id}")])
    rows.append([InlineKeyboardButton(text="Добавить", callback_data="traders:add")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


