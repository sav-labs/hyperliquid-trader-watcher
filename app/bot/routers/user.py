from __future__ import annotations

import logging
import re
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards import main_keyboard, main_menu_kb, traders_list_kb, traders_menu_kb
from app.bot.states import UserStates
from app.db.engine import Database
from app.db.models import UserStatus
from app.db.repositories import TraderRepository, UserRepository
from app.hyperliquid.client import HyperliquidClient
from settings import Settings

logger = logging.getLogger(__name__)

router = Router(name="user")

_ADDR_RE = re.compile(r"0x[a-fA-F0-9]{40}")


def _short_addr(a: str) -> str:
    a = a.lower()
    return f"{a[:6]}…{a[-4:]}"


def _fmt_number(val: str | float) -> str:
    """Format number with thousand separators."""
    try:
        num = float(val)
        if abs(num) >= 1_000_000:
            return f"{num:,.2f}"
        elif abs(num) >= 1_000:
            return f"{num:,.2f}"
        else:
            return f"{num:.2f}"
    except (ValueError, TypeError):
        return str(val)


def _format_timestamp(ts_ms: int) -> str:
    """Format timestamp from milliseconds to human-readable."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, OSError):
        return "???"


@router.message(F.text == "/start")
async def start(message: Message, db: Database, settings: Settings) -> None:
    tg = message.from_user
    if tg is None:
        return

    async with db.sessionmaker() as session:
        users = UserRepository(session)
        user = await users.get_or_create(telegram_id=tg.id, username=tg.username)

        # Auto-approve and set admin flag for admins
        if tg.id in settings.bot_admins:
            if not user.is_admin:
            user.is_admin = True
            if user.status != UserStatus.approved:
                user.status = UserStatus.approved

        await session.commit()

    if user.status != UserStatus.approved:
        await message.answer(
            "Доступ к боту выдаётся администраторами.\n"
            "Ваша заявка зафиксирована — ожидайте подтверждения."
        )

        # Notify admins
        for admin_id in settings.bot_admins:
            try:
                await message.bot.send_message(
                    chat_id=admin_id,
                    text=f"Новая заявка: @{tg.username or '—'} (id={tg.id})",
                    reply_markup=__admin_quick_kb(tg.id),
                )
            except Exception:
                logger.exception("Failed to notify admin %s", admin_id)
        return

    # Show permanent keyboard
    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Используйте кнопки ниже для навигации:",
        reply_markup=main_keyboard(is_admin=user.is_admin)
    )


def __admin_quick_kb(user_tg_id: int):
    from app.bot.keyboards import admin_request_kb

    return admin_request_kb(user_tg_id)


@router.message(F.text == "/menu")
async def menu(message: Message, db: Database) -> None:
    """Legacy menu command - kept for backwards compatibility."""
    tg = message.from_user
    if tg is None:
        return
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        user = await users.get_by_telegram_id(tg.id)
        if user is None or user.status != UserStatus.approved:
            await message.answer("Нет доступа. Нажмите /start и дождитесь одобрения.")
            return
        await message.answer("Меню:", reply_markup=main_menu_kb(is_admin=user.is_admin))


# ============= Reply keyboard handlers (text buttons) =============

@router.message(F.text == "Трейдеры")
async def traders_button(message: Message, db: Database, hl: HyperliquidClient) -> None:
    """Handle 'Трейдеры' button from reply keyboard."""
    await _send_traders_list(message, db, hl)


@router.message(F.text == "Админ-панель")
async def admin_panel_button(message: Message, db: Database) -> None:
    """Handle 'Админ-панель' button from reply keyboard."""
    from app.bot.keyboards import admin_menu_kb
    from app.db.models import UserStatus
    
    tg = message.from_user
    if tg is None:
        return
    
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        user = await users.get_by_telegram_id(tg.id)
        
        if user is None or user.status != UserStatus.approved:
            await message.answer("Нет доступа.")
            return
        
        if not user.is_admin:
            await message.answer("Эта функция доступна только администраторам.")
            return
        
        # Count pending requests
        pending = await users.list_by_status(UserStatus.pending)
        
    await message.answer(
        f"Админ-панель\n\nНовых заявок: {len(pending)}",
        reply_markup=admin_menu_kb(pending_count=len(pending))
    )


@router.message(F.text == "Настройки")
async def settings_button(message: Message, db: Database) -> None:
    """Handle 'Настройки' button from reply keyboard."""
    tg = message.from_user
    if tg is None:
        return
    
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        user = await users.get_by_telegram_id(tg.id)
        
        if user is None or user.status != UserStatus.approved:
            await message.answer("Нет доступа.")
            return
        
        if not user.is_admin:
            await message.answer("Эта функция доступна только администраторам.")
            return
        
        mode = user.delivery_mode.value
        chat = user.delivery_chat_id or ""
    
    await message.answer(
        "⚙️ Настройки доставки:\n\n"
        f"Текущий режим: {mode} {chat}\n\n"
        "По умолчанию алерты приходят в ЛС.\n"
        "Настройку отправки в канал делает администратор."
    )


@router.callback_query(F.data == "menu:back")
async def back(call: CallbackQuery, db: Database) -> None:
    tg = call.from_user
    if tg is None:
        return
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        user = await users.get_by_telegram_id(tg.id)
        if user is None or user.status != UserStatus.approved:
            await call.answer("Нет доступа", show_alert=True)
            return
        await call.message.edit_text("Меню:", reply_markup=main_menu_kb(is_admin=user.is_admin))
    await call.answer()


@router.callback_query(F.data == "menu:traders")
async def traders_menu(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    await _edit_traders_list(call, db, hl)
    await call.answer()


@router.callback_query(F.data == "traders:list")
async def traders_list_callback(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    """Return to traders list."""
    await _edit_traders_list(call, db, hl)
    await call.answer()


@router.callback_query(F.data == "menu:settings")
async def settings_menu(call: CallbackQuery, db: Database) -> None:
    tg = call.from_user
    if tg is None:
        return
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        user = await users.get_by_telegram_id(tg.id)
        if user is None or user.status != UserStatus.approved:
            await call.answer("Нет доступа", show_alert=True)
            return

        mode = user.delivery_mode.value
        chat = user.delivery_chat_id or ""

    try:
        await call.message.edit_text(
            "Настройки доставки:\n"
            f"- Текущий режим: {mode} {chat}\n\n"
            "По умолчанию алерты приходят в ЛС.\n"
            "Настройку отправки в канал делает администратор.",
            reply_markup=main_menu_kb(is_admin=user.is_admin),
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise
    await call.answer()


@router.callback_query(F.data == "traders:add")
async def traders_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserStates.adding_traders)
    await call.message.answer(
        "Отправьте адреса трейдеров одним сообщением.\n"
        "Можно по одному или списком (каждый с новой строки). Пример:\n"
        "0x0000000000000000000000000000000000000000"
    )
    await call.answer()


@router.message(UserStates.adding_traders, F.text)
async def traders_add_text(message: Message, db: Database, state: FSMContext, hl: HyperliquidClient) -> None:
    tg = message.from_user
    if tg is None:
        return

    found = _ADDR_RE.findall(message.text or "")
    addrs = sorted({a.lower() for a in found})
    if not addrs:
        await message.answer("Не нашёл ни одного адреса. Пришлите 0x… адрес(а).")
        return

    added = 0
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        traders = TraderRepository(session)

        user = await users.get_by_telegram_id(tg.id)
        if user is None or user.status != UserStatus.approved:
            await message.answer("Нет доступа. Нажмите /start.")
            await state.clear()
            return

        for a in addrs:
            try:
                await traders.add_trader_to_user(user, a)
                await session.commit()  # Commit immediately after each trader
                added += 1
            except Exception:
                # likely unique constraint
                logger.debug("Could not add trader %s for user %s", a, tg.id, exc_info=True)
                await session.rollback()  # Rollback on error to continue loop

    await state.clear()
    await message.answer(f"Готово. Добавлено: {added}/{len(addrs)}")
    await _send_traders_list(message, db, hl)


@router.callback_query(F.data == "traders:list")
async def traders_list(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    await _edit_traders_list(call, db, hl)
    await call.answer()


@router.callback_query(F.data.startswith("traders:view:"))
async def traders_view(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    """Show detailed trader card with live data."""
    tg = call.from_user
    if tg is None:
        return

    trader_id = int(call.data.split(":")[-1])
    await _show_trader_details(call, db, hl, trader_id, edit=True)


@router.callback_query(F.data.startswith("traders:refresh:"))
async def traders_refresh(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    """Refresh trader details."""
    tg = call.from_user
    if tg is None:
        return

    trader_id = int(call.data.split(":")[-1])
    await call.answer("Обновляю...")
    await _show_trader_details(call, db, hl, trader_id, edit=True)


@router.callback_query(F.data.startswith("traders:sort:"))
async def traders_sort(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    """Sort positions by PnL or Position Value."""
    tg = call.from_user
    if tg is None:
        return
    
    # Parse callback data: traders:sort:{trader_id}:{sort_by}
    parts = call.data.split(":")
    if len(parts) < 4:
        await call.answer("Неверный формат", show_alert=True)
        return
    
    trader_id = int(parts[2])
    sort_by = parts[3]  # "pnl" or "value"
    
    await call.answer()
    await _show_trader_details(call, db, hl, trader_id, edit=True, sort_by=sort_by)


@router.callback_query(F.data.startswith("traders:history:"))
async def traders_history(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    """Show deposit/withdrawal history."""
    tg = call.from_user
    if tg is None:
        return

    trader_id = int(call.data.split(":")[-1])
    
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        traders_repo = TraderRepository(session)
        
        user = await users.get_by_telegram_id(tg.id)
        if user is None or user.status != UserStatus.approved:
            await call.answer("Нет доступа", show_alert=True)
            return
        
        # Find trader
        user_traders = await traders_repo.list_user_traders(user)
        trader = next((t for t in user_traders if t.id == trader_id), None)
        if trader is None:
            await call.answer("Трейдер не найден", show_alert=True)
            return
    
    await call.answer("Загружаю историю...")
    
    # Fetch fresh ledger updates (deposits/withdrawals)
    ledger_updates = await hl.fetch_recent_ledger_updates(trader.address, limit=20)
    
    if not ledger_updates:
        text = f"📊 История: {_short_addr(trader.address)}\n\nИстория пуста."
    else:
        text = f"📊 История: {_short_addr(trader.address)}\n\n"
        for upd in ledger_updates[:10]:  # Last 10 entries
            delta = upd.get("delta", {})
            timestamp = upd.get("time", 0)
            dt_str = _format_timestamp(timestamp)
            
            # Parse delta structure
            # Delta can be: {"type": "deposit", "usdc": "amount"} or similar
            delta_type = delta.get("type", "unknown")
            usdc_amount = delta.get("usdc", "0")
            
            # Also check for "total" field
            if not usdc_amount or usdc_amount == "0":
                usdc_amount = delta.get("total", "0")
            
            try:
                amount_float = float(usdc_amount)
            except (ValueError, TypeError):
                amount_float = 0
            
            if delta_type == "deposit" or amount_float > 0:
                text += f"✅ Депозит: +${_fmt_number(abs(amount_float))} ({dt_str})\n"
            elif delta_type == "withdraw" or amount_float < 0:
                text += f"❌ Вывод: ${_fmt_number(abs(amount_float))} ({dt_str})\n"
            else:
                # Fallback: show raw data for debugging
                text += f"🔹 {delta_type}: ${_fmt_number(abs(amount_float))} ({dt_str})\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data=f"traders:view:{trader_id}")]
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        # If message not modified, just ignore
        if "message is not modified" not in str(e).lower():
            raise


@router.callback_query(F.data.startswith("traders:remove:"))
async def traders_remove(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    """Remove trader from user's list."""
    tg = call.from_user
    if tg is None:
        return

    trader_id = int(call.data.split(":")[-1])
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        traders = TraderRepository(session)

        user = await users.get_by_telegram_id(tg.id)
        if user is None or user.status != UserStatus.approved:
            await call.answer("Нет доступа", show_alert=True)
            return

        await traders.remove_trader_from_user(user, trader_id)
        await session.commit()

    await call.answer("Удалено")
    await _edit_traders_list(call, db, hl)


@router.callback_query(F.data.startswith("traders:position:"))
async def traders_position(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    """Show detailed position information."""
    tg = call.from_user
    if tg is None:
        return
    
    # Parse callback data: traders:position:{trader_id}:{coin}
    parts = call.data.split(":")
    if len(parts) < 4:
        await call.answer("Неверный формат", show_alert=True)
        return
    
    trader_id = int(parts[2])
    coin = parts[3]
    
    await _show_position_detail(call, db, hl, trader_id, coin)


@router.callback_query(F.data.startswith("traders:fills:"))
async def traders_fills(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    """Show full trade history (fills) for a position."""
    tg = call.from_user
    if tg is None:
        return
    
    # Parse callback data: traders:fills:{trader_id}:{coin}
    parts = call.data.split(":")
    if len(parts) < 4:
        await call.answer("Неверный формат", show_alert=True)
        return
    
    trader_id = int(parts[2])
    coin = parts[3]
    
    await _show_position_fills(call, db, hl, trader_id, coin)


async def _show_position_detail(call: CallbackQuery, db: Database, hl: HyperliquidClient, trader_id: int, coin: str) -> None:
    """Show detailed position information with history."""
    tg = call.from_user
    if tg is None:
        return
    
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        traders_repo = TraderRepository(session)
        
        user = await users.get_by_telegram_id(tg.id)
        if user is None or user.status != UserStatus.approved:
            await call.answer("Нет доступа", show_alert=True)
            return
        
        # Find trader
        user_traders = await traders_repo.list_user_traders(user)
        trader = next((t for t in user_traders if t.id == trader_id), None)
        if trader is None:
            await call.answer("Трейдер не найден", show_alert=True)
            return
    
    # Fetch current state
    try:
        snapshot = await hl.fetch_user_state(trader.address)
    except Exception:
        logger.exception("Failed to fetch trader state")
        await call.answer("Ошибка загрузки данных", show_alert=True)
        return
    
    # Find position
    user_state = snapshot.user_state
    positions = user_state.get("assetPositions", [])
    position_data = None
    
    for pos in positions:
        p = pos.get("position", {})
        if p.get("coin") == coin:
            position_data = p
            break
    
    if not position_data:
        await call.answer(f"Позиция {coin} не найдена", show_alert=True)
        return
    
    # Extract position details
    szi = position_data.get("szi", "0")
    entry_px = position_data.get("entryPx", "0")
    leverage_info = position_data.get("leverage", {})
    leverage_val = leverage_info.get("value", 1) if isinstance(leverage_info, dict) else 1
    unrealized_pnl = position_data.get("unrealizedPnl", "0")
    position_value_str = position_data.get("positionValue", "0")
    liquidation_px = position_data.get("liquidationPx")
    max_trade_szs = position_data.get("maxTradeSzs", [])
    
    # Calculate metrics
    side = "🟢 LONG" if float(szi) > 0 else "🔴 SHORT"
    size_abs = abs(float(szi))
    upnl_float = float(unrealized_pnl)
    position_value = abs(float(position_value_str))
    
    # Calculate current price and margin
    current_price = 0.0
    margin_used = 0.0
    position_roe = 0.0
    
    try:
        size = float(szi)
        entry_price = float(entry_px)
        lev = float(leverage_val)
        
        # Current Price = Entry Price + (Unrealized PnL / Size)
        if size != 0:
            current_price = entry_price + (upnl_float / size)
        else:
            current_price = entry_price
        
        # Margin used
        if lev > 0 and current_price > 0:
            calc_position_value = abs(size) * current_price
            margin_used = calc_position_value / lev
            if margin_used > 0:
                position_roe = (upnl_float / margin_used) * 100
    except (ValueError, TypeError, ZeroDivisionError):
        pass
    
    # Format message
    text = f"📊 **Позиция: {coin}**\n\n"
    text += f"{side}\n\n"
    
    text += f"💰 **Position Value / Size:**\n"
    text += f"  ${_fmt_number(str(position_value))}\n"
    text += f"  {_fmt_number(str(size_abs))} {coin}\n\n"
    text += f"📊 **Цены:**\n"
    text += f"  • Входная цена: ${_fmt_number(entry_px)}\n"
    if current_price > 0:
        text += f"  • Текущая цена: ${_fmt_number(str(current_price))}\n"
    if liquidation_px:
        text += f"  • Цена ликвидации: ${_fmt_number(str(liquidation_px))}\n"
    
    text += f"\n⚙️ **Плечо и маржа:**\n"
    text += f"  • Плечо: {leverage_val}x\n"
    text += f"  • Маржа использована: ${_fmt_number(str(margin_used))}\n"
    
    # PnL
    upnl_sign = "+" if upnl_float >= 0 else ""
    roe_sign = "+" if position_roe >= 0 else ""
    pnl_emoji = "📈" if upnl_float >= 0 else "📉"
    text += f"\n{pnl_emoji} **PnL:**\n"
    text += f"  • Unrealized: {upnl_sign}${_fmt_number(str(abs(upnl_float)))}\n"
    text += f"  • ROE: {roe_sign}{abs(position_roe):.2f}%\n"
    
    # Max trade sizes (if available)
    if max_trade_szs:
        text += f"\n📊 **Max Trade Sizes:**\n"
        for mts in max_trade_szs[:3]:  # Show first 3
            text += f"  • {_fmt_number(str(mts))} {coin}\n"
    
    from app.bot.keyboards import position_detail_kb
    
    try:
        await call.message.edit_text(text, reply_markup=position_detail_kb(trader_id, coin), parse_mode="Markdown")
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise


async def _show_position_fills(call: CallbackQuery, db: Database, hl: HyperliquidClient, trader_id: int, coin: str) -> None:
    """Show full trade history (fills) for a position."""
    tg = call.from_user
    if tg is None:
        return
    
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        traders_repo = TraderRepository(session)
        
        user = await users.get_by_telegram_id(tg.id)
        if user is None or user.status != UserStatus.approved:
            await call.answer("Нет доступа", show_alert=True)
            return
        
        # Find trader
        user_traders = await traders_repo.list_user_traders(user)
        trader = next((t for t in user_traders if t.id == trader_id), None)
        if trader is None:
            await call.answer("Трейдер не найден", show_alert=True)
            return
    
    await call.answer("Загружаю историю сделок...")
    
    # Fetch current position to determine side
    try:
        snapshot = await hl.fetch_user_state(trader.address)
        positions = snapshot.user_state.get("assetPositions", [])
        position_data = None
        for pos in positions:
            p = pos.get("position", {})
            if p.get("coin") == coin:
                position_data = p
                break
        
        position_side = None
        if position_data:
            szi = position_data.get("szi", "0")
            position_side = "LONG" if float(szi) > 0 else "SHORT"
    except Exception:
        position_side = None
    
    # Fetch ALL fills for this coin (no limit)
    try:
        fills = await hl.fetch_user_fills(trader.address, coin, limit=1000)
    except Exception as e:
        logger.error(f"Failed to fetch fills for {coin}: {e}", exc_info=True)
        await call.answer("Ошибка получения данных", show_alert=True)
        return
    
    if not fills:
        text = f"📜 **История сделок: {coin}**\n\n"
        text += "_Нет данных о сделках_\n"
    else:
        text = f"📜 **История сделок: {coin}**\n\n"
        text += f"_История исполненных ордеров по этой позиции_\n\n"
        text += f"📊 Всего сделок: **{len(fills)}**\n"
        
        # Add explanation based on position side
        if position_side == "SHORT":
            text += f"🔴 Текущая позиция: **SHORT**\n"
            text += f"_• SELL = открытие/увеличение SHORT_\n"
            text += f"_• BUY = закрытие/уменьшение SHORT_\n\n"
        elif position_side == "LONG":
            text += f"🟢 Текущая позиция: **LONG**\n"
            text += f"_• BUY = открытие/увеличение LONG_\n"
            text += f"_• SELL = закрытие/уменьшение LONG_\n\n"
        else:
            text += "\n"
        
        # Show all fills with detailed info
        for fill in fills:
            fill_time = _format_timestamp(fill.get("time", 0))
            fill_px = fill.get("px", "0")
            fill_sz = fill.get("sz", "0")
            fill_side = fill.get("side", "")
            fill_fee = fill.get("fee", "0")
            
            # Side determination: "A" = sell/short, "B" = buy/long
            if fill_side == "B":
                side_emoji = "🟢"
                side_text = "BUY"
                action_text = "Куплено"
            elif fill_side == "A":
                side_emoji = "🔴"
                side_text = "SELL"
                action_text = "Продано"
            else:
                side_emoji = "⚪️"
                side_text = fill_side
                action_text = "Сделка"
            
            # Calculate total trade value
            try:
                trade_value = float(fill_sz) * float(fill_px)
                trade_value_str = f"${_fmt_number(str(trade_value))}"
            except (ValueError, TypeError):
                trade_value_str = "???"
            
            text += f"{side_emoji} **{side_text}** {_fmt_number(fill_sz)} {coin}\n"
            text += f"  • Цена: ${_fmt_number(fill_px)}\n"
            text += f"  • Сумма: {trade_value_str}\n"
            text += f"  • Комиссия: ${_fmt_number(fill_fee)}\n"
            text += f"  • Время: {fill_time}\n\n"
    
    from app.bot.keyboards import position_fills_kb
    
    try:
        await call.message.edit_text(text, reply_markup=position_fills_kb(trader_id, coin), parse_mode="Markdown")
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise


async def _show_trader_details(call: CallbackQuery, db: Database, hl: HyperliquidClient, trader_id: int, edit: bool = True, sort_by: str = "value") -> None:
    """
    Show detailed trader information with live data.
    sort_by: "pnl" or "value" - how to sort positions
    """
    tg = call.from_user
    if tg is None:
        return
    
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        traders_repo = TraderRepository(session)
        
        user = await users.get_by_telegram_id(tg.id)
        if user is None or user.status != UserStatus.approved:
            await call.answer("Нет доступа", show_alert=True)
            return
        
        # Find trader
        user_traders = await traders_repo.list_user_traders(user)
        trader = next((t for t in user_traders if t.id == trader_id), None)
        if trader is None:
            await call.answer("Трейдер не найден", show_alert=True)
            return
    
    # Fetch fresh data from Hyperliquid API
    try:
        snapshot = await hl.fetch_user_state(trader.address)
    except Exception as e:
        logger.error(f"Failed to fetch trader state: {e}", exc_info=True)
        await call.answer("Ошибка получения данных", show_alert=True)
        return
    
    user_state = snapshot.user_state
    
    # Parse data
    account_value = snapshot.account_value or "0"  # Total (Combined)
    perp_value = snapshot.perp_value or "0"
    spot_value = snapshot.spot_value or "0"
    withdrawable = snapshot.withdrawable or "0"
    total_position_value = snapshot.total_position_value
    
    # Calculate leverage: totalPositionValue / Perp (as on HyperDash)
    # Leverage applies only to Perp positions, not Spot
    leverage_multiplier = 0.0
    try:
        perp_value_float = float(perp_value)
        if perp_value_float > 0 and total_position_value > 0:
            leverage_multiplier = total_position_value / perp_value_float
    except (ValueError, TypeError, ZeroDivisionError):
        pass
    
    # Use totalMarginUsed from API if available (more accurate)
    margin_summary = user_state.get("marginSummary", {}) or user_state.get("crossMarginSummary", {})
    total_margin_used_from_api = margin_summary.get("totalMarginUsed", None)
    
    # Positions
    positions = user_state.get("assetPositions", [])
    
    # Calculate Unrealized PnL from all positions
    unrealized_pnl = 0.0
    for pos in positions:
        position = pos.get("position", {})
        upnl = position.get("unrealizedPnl", "0")
        try:
            unrealized_pnl += float(upnl)
        except (ValueError, TypeError):
            pass
    
    # Use API's totalMarginUsed for ROE calculation (most accurate)
    pnl_percent = 0.0
    if total_margin_used_from_api:
        try:
            total_margin_used = float(total_margin_used_from_api)
            if total_margin_used > 0:
                pnl_percent = (unrealized_pnl / total_margin_used) * 100
        except (ValueError, TypeError, ZeroDivisionError):
            total_margin_used_from_api = None
    
    # Fallback: calculate margin used if API doesn't provide it
    if not total_margin_used_from_api:
        total_margin_used = 0.0
        for pos in positions:
            position = pos.get("position", {})
            upnl = position.get("unrealizedPnl", "0")
            szi = position.get("szi", "0")
            entry_px = position.get("entryPx", "0")
            leverage_info = position.get("leverage", {})
            leverage_val = leverage_info.get("value", 1) if isinstance(leverage_info, dict) else 1
            
            try:
                upnl_float = float(upnl)
                size = float(szi)
                entry_price = float(entry_px)
                leverage = float(leverage_val)
                
                # Calculate current price from unrealized PnL
                if size != 0:
                    current_price = entry_price + (upnl_float / size)
                else:
                    current_price = entry_price
                
                # Calculate margin used based on CURRENT price
                if leverage > 0 and current_price > 0:
                    position_value = abs(size) * current_price
                    margin_used = position_value / leverage
                    total_margin_used += margin_used
            except (ValueError, TypeError, ZeroDivisionError):
                pass
        
        if total_margin_used > 0:
            pnl_percent = (unrealized_pnl / total_margin_used) * 100
    
    # Format message with detailed breakdown like HyperDash
    text = f"📊 Трейдер: `{trader.address}`\n\n"
    
    # Total Value (Combined) with Perp and Spot breakdown
    text += f"💰 **Total Value (Combined):** ${_fmt_number(account_value)}\n"
    text += f"   • Perp: ${_fmt_number(perp_value)}\n"
    text += f"   • Spot: ${_fmt_number(spot_value)}\n\n"
    
    # Withdrawable amount (% calculated from Perp equity, as on HyperDash)
    try:
        withdrawable_float = float(withdrawable)
        perp_value_float = float(perp_value)
        withdrawable_percent = (withdrawable_float / perp_value_float * 100) if perp_value_float > 0 else 0
        text += f"💵 **Withdrawable:** ${_fmt_number(withdrawable)} ({withdrawable_percent:.2f}%)\n"
    except (ValueError, TypeError, ZeroDivisionError):
        text += f"💵 **Withdrawable:** ${_fmt_number(withdrawable)}\n"
    
    # Leverage
    if leverage_multiplier > 0:
        text += f"📊 **Leverage:** {leverage_multiplier:.2f}x (${_fmt_number(str(total_position_value))})\n"
    else:
        text += f"📊 **Leverage:** 0x (нет позиций)\n"
    
    pnl_emoji = "📈" if unrealized_pnl >= 0 else "📉"
    pnl_sign = "+" if unrealized_pnl >= 0 else "-"
    text += f"{pnl_emoji} **Unrealized PnL:** {pnl_sign}${_fmt_number(str(abs(unrealized_pnl)))} ({pnl_sign}{abs(pnl_percent):.2f}%)\n\n"
    
    # Prepare position list for inline buttons
    position_buttons = []
    if positions:
        text += f"**🔹 Открытые позиции ({len(positions)}):**\n"
        text += "_Нажмите на позицию для деталей_\n"
        
        for pos in positions:
            position = pos.get("position", {})
            coin = position.get("coin", "???")
            szi = position.get("szi", "0")
            entry_px = position.get("entryPx", "0")
            leverage_val = position.get("leverage", {}).get("value", 1)
            pos_unrealized_pnl = position.get("unrealizedPnl", "0")
            
            side = "🟢 LONG" if float(szi) > 0 else "🔴 SHORT"
            
            # Calculate position value
            upnl_float = float(pos_unrealized_pnl)
            position_value = 0.0
            
            try:
                size = float(szi) if szi else 0
                entry_price = float(entry_px) if entry_px else 0
                
                # Calculate current price
                if size != 0:
                    current_price = entry_price + (upnl_float / size)
                else:
                    current_price = entry_price
                
                # Position value in USD
                if current_price > 0:
                    position_value = abs(size) * current_price
            except (ValueError, TypeError, ZeroDivisionError):
                pass
            
            position_buttons.append({
                "coin": coin,
                "side": side,
                "unrealized_pnl": upnl_float,
                "position_value": position_value,
            })
        
        # Sort positions based on selected criteria
        if sort_by == "pnl":
            # Sort by unrealized PnL (descending - highest profit first)
            position_buttons.sort(key=lambda x: x["unrealized_pnl"], reverse=True)
        else:  # sort_by == "value"
            # Sort by position value (descending - largest position first)
            position_buttons.sort(key=lambda x: x["position_value"], reverse=True)
    else:
        text += "📭 Нет открытых позиций\n"
    
    from app.bot.keyboards import trader_detail_kb
    
    if edit:
        try:
            await call.message.edit_text(
                text, 
                reply_markup=trader_detail_kb(trader_id, position_buttons if position_buttons else None, sort_by), 
                parse_mode="Markdown"
            )
        except Exception as e:
            # If message not modified, just ignore
            if "message is not modified" not in str(e).lower():
                raise
    else:
        await call.message.answer(
            text, 
            reply_markup=trader_detail_kb(trader_id, position_buttons if position_buttons else None, sort_by), 
            parse_mode="Markdown"
        )


async def _send_traders_list(message: Message, db: Database, hl: HyperliquidClient) -> None:
    tg = message.from_user
    if tg is None:
        return

    async with db.sessionmaker() as session:
        users = UserRepository(session)
        traders_repo = TraderRepository(session)
        user = await users.get_by_telegram_id(tg.id)
        if user is None or user.status != UserStatus.approved:
            await message.answer("Нет доступа.")
            return

        traders = await traders_repo.list_user_traders(user)
        await _refresh_balances_if_needed(session, hl, traders)
        await session.commit()
        items = [(t.id, _short_addr(t.address), (t.state.last_account_value if t.state else None)) for t in traders]

    if not items:
        await message.answer("Ваш список трейдеров пуст.", reply_markup=traders_menu_kb())
        return

    await message.answer("Ваши трейдеры (нажмите для удаления):", reply_markup=traders_list_kb(items))


async def _edit_traders_list(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    tg = call.from_user
    if tg is None:
        return
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        traders_repo = TraderRepository(session)
        user = await users.get_by_telegram_id(tg.id)
        if user is None or user.status != UserStatus.approved:
            await call.answer("Нет доступа", show_alert=True)
            return

        traders = await traders_repo.list_user_traders(user)
        await _refresh_balances_if_needed(session, hl, traders)
        await session.commit()
        items = [(t.id, _short_addr(t.address), (t.state.last_account_value if t.state else None)) for t in traders]

    if not items:
        await call.message.edit_text("Ваш список трейдеров пуст.", reply_markup=traders_menu_kb())
        return

    await call.message.edit_text("Ваши трейдеры (нажмите для удаления):", reply_markup=traders_list_kb(items))


async def _refresh_balances_if_needed(session, hl: HyperliquidClient, traders) -> None:
    """
    Best-effort refresh of trader balances to show 'current balance' in inline buttons.
    We update when balance is missing or stale (>30s old by TraderState.updated_at).
    """
    now = time.time()
    to_refresh = []
    for t in traders:
        st = t.state
        if st is None:
            continue
        if st.last_account_value is None:
            to_refresh.append(t)
            continue
        try:
            age = now - (st.updated_at.timestamp())
        except Exception:
            age = 9999
        if age > 30:
            to_refresh.append(t)

    # Limit per-request network calls
    to_refresh = to_refresh[:5]
    for t in to_refresh:
        try:
            snap = await hl.fetch_user_state(t.address)
            if t.state is not None and snap.account_value is not None:
                t.state.last_account_value = snap.account_value
        except Exception:
            logger.debug("Balance refresh failed for %s", t.address, exc_info=True)


