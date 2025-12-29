from __future__ import annotations

import logging
import re
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

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
                added += 1
            except Exception:
                # likely unique constraint
                logger.debug("Could not add trader %s for user %s", a, tg.id, exc_info=True)

        await session.commit()

    await state.clear()
    await message.answer(f"Готово. Добавлено: {added}/{len(addrs)}")
    await _send_traders_list(message, db, hl)


@router.callback_query(F.data == "traders:list")
async def traders_list(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
    await _edit_traders_list(call, db, hl)
    await call.answer()


@router.callback_query(F.data.startswith("traders:remove:"))
async def traders_remove(call: CallbackQuery, db: Database, hl: HyperliquidClient) -> None:
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


