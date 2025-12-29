from __future__ import annotations

import logging
import re
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards import main_menu_kb, traders_list_kb, traders_menu_kb
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

        if tg.id in settings.bot_admins and not user.is_admin:
            user.is_admin = True

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

    await message.answer("Меню:", reply_markup=main_menu_kb(is_admin=user.is_admin))


def __admin_quick_kb(user_tg_id: int):
    from app.bot.keyboards import admin_request_kb

    return admin_request_kb(user_tg_id)


@router.message(F.text == "/menu")
async def menu(message: Message, db: Database) -> None:
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

    await call.message.edit_text(
        "Настройки доставки:\n"
        f"- Текущий режим: {mode} {chat}\n\n"
        "По умолчанию алерты приходят в ЛС.\n"
        "Настройку отправки в канал делает администратор.",
        reply_markup=main_menu_kb(is_admin=user.is_admin),
    )
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
    
    # Debug: log first entry structure
    if ledger_updates:
        logger.info(f"Ledger update sample: {ledger_updates[0]}")
    
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


async def _show_trader_details(call: CallbackQuery, db: Database, hl: HyperliquidClient, trader_id: int, edit: bool = True) -> None:
    """Show detailed trader information with live data."""
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
    margin_summary = user_state.get("marginSummary", {})
    
    # Debug: log margin summary to understand available fields
    logger.info(f"Margin summary keys: {list(margin_summary.keys())}")
    logger.info(f"Margin summary: {margin_summary}")
    
    account_value = margin_summary.get("accountValue", "0")
    total_ntl_pos = margin_summary.get("totalNtlPos", "0")
    
    # Positions
    positions = user_state.get("assetPositions", [])
    
    # Calculate Unrealized PnL and Total Margin Used from all positions
    unrealized_pnl = 0.0
    total_margin_used = 0.0
    
    for pos in positions:
        position = pos.get("position", {})
        upnl = position.get("unrealizedPnl", "0")
        try:
            unrealized_pnl += float(upnl)
        except (ValueError, TypeError):
            pass
        
        # Calculate margin used for this position
        # Margin Used = Position Value / Leverage
        szi = position.get("szi", "0")
        entry_px = position.get("entryPx", "0")
        leverage_info = position.get("leverage", {})
        leverage_val = leverage_info.get("value", 1) if isinstance(leverage_info, dict) else 1
        
        try:
            size = abs(float(szi))
            price = float(entry_px)
            leverage = float(leverage_val)
            
            if leverage > 0:
                position_value = size * price
                margin_used = position_value / leverage
                total_margin_used += margin_used
        except (ValueError, TypeError, ZeroDivisionError):
            pass
    
    # Calculate ROE (Return On Equity) - percentage based on margin used
    # This is how HyperDash calculates it
    logger.info(f"Total margin used: ${total_margin_used:,.2f}, Unrealized PnL: ${unrealized_pnl:,.2f}")
    
    pnl_percent = 0.0
    if total_margin_used > 0:
        pnl_percent = (unrealized_pnl / total_margin_used) * 100
        logger.info(f"ROE calculated: {pnl_percent:.2f}%")
    elif float(account_value) > 0:
        # Fallback to account value if margin calculation fails
        pnl_percent = (unrealized_pnl / float(account_value)) * 100
        logger.info(f"ROE calculated (fallback): {pnl_percent:.2f}%")
    
    # Format message
    text = f"📊 Трейдер: `{trader.address}`\n\n"
    text += f"💰 **Баланс:** ${_fmt_number(account_value)}\n"
    
    pnl_emoji = "📈" if unrealized_pnl >= 0 else "📉"
    pnl_sign = "+" if unrealized_pnl >= 0 else "-"
    text += f"{pnl_emoji} **Unrealized PnL:** {pnl_sign}${_fmt_number(str(abs(unrealized_pnl)))} ({pnl_sign}{abs(pnl_percent):.2f}%)\n\n"
    
    if positions:
        text += "**🔹 Открытые позиции:**\n\n"
        for pos in positions:
            position = pos.get("position", {})
            coin = position.get("coin", "???")
            szi = position.get("szi", "0")
            entry_px = position.get("entryPx", "0")
            leverage_val = position.get("leverage", {}).get("value", 1)
            unrealized_pnl = position.get("unrealizedPnl", "0")
            
            side = "🟢 LONG" if float(szi) > 0 else "🔴 SHORT"
            size_abs = abs(float(szi))
            
            # Calculate ROE for this position
            upnl_float = float(unrealized_pnl)
            position_roe = 0.0
            try:
                size = float(szi) if szi else 0
                price = float(entry_px) if entry_px else 0
                lev = float(leverage_val) if leverage_val else 1
                
                if lev > 0 and price > 0:
                    position_value = abs(size) * price
                    margin_used_pos = position_value / lev
                    if margin_used_pos > 0:
                        position_roe = (upnl_float / margin_used_pos) * 100
            except (ValueError, TypeError, ZeroDivisionError):
                pass
            
            text += f"{side} **{coin}**\n"
            text += f"  ├ Размер: {_fmt_number(str(size_abs))} {coin}\n"
            text += f"  ├ Входная цена: ${_fmt_number(entry_px)}\n"
            text += f"  ├ Плечо: {leverage_val}x\n"
            upnl_sign = "+" if upnl_float >= 0 else "-"
            roe_sign = "+" if position_roe >= 0 else "-"
            text += f"  └ PnL: {upnl_sign}${_fmt_number(str(abs(upnl_float)))} ({roe_sign}{abs(position_roe):.2f}%)\n\n"
    else:
        text += "📭 Нет открытых позиций\n"
    
    from app.bot.keyboards import trader_detail_kb
    
    if edit:
        try:
            await call.message.edit_text(text, reply_markup=trader_detail_kb(trader_id), parse_mode="Markdown")
        except Exception as e:
            # If message not modified, just ignore
            if "message is not modified" not in str(e).lower():
                raise
    else:
        await call.message.answer(text, reply_markup=trader_detail_kb(trader_id), parse_mode="Markdown")


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


