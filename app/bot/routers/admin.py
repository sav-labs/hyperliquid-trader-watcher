from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import admin_menu_kb, admin_user_kb, main_menu_kb
from app.bot.states import AdminStates
from app.db.engine import Database
from app.db.models import UserStatus
from app.db.repositories import UserRepository
from settings import Settings

logger = logging.getLogger(__name__)

router = Router(name="admin")


def _is_admin(tg_id: int, user_is_admin: bool, settings: Settings) -> bool:
    return user_is_admin or (tg_id in settings.bot_admins)


@router.message(F.text == "/admin")
async def admin_cmd(message: Message, db: Database, settings: Settings) -> None:
    tg = message.from_user
    if tg is None:
        return

    async with db.sessionmaker() as session:
        users = UserRepository(session)
        user = await users.get_or_create(tg.id, tg.username)
        if tg.id in settings.bot_admins:
            user.is_admin = True
        await session.commit()

        if not _is_admin(tg.id, user.is_admin, settings):
            await message.answer("Недостаточно прав.")
            return

        pending = await users.list_pending()

    await message.answer("Админ-панель:", reply_markup=admin_menu_kb(pending_count=len(pending)))


@router.callback_query(F.data == "menu:admin")
async def admin_menu(call: CallbackQuery, db: Database, settings: Settings) -> None:
    tg = call.from_user
    if tg is None:
        return
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        user = await users.get_or_create(tg.id, tg.username)
        if tg.id in settings.bot_admins:
            user.is_admin = True
        await session.commit()

        if not _is_admin(tg.id, user.is_admin, settings):
            await call.answer("Недостаточно прав", show_alert=True)
            return

        pending = await users.list_pending()

    await call.message.edit_text("Админ-панель:", reply_markup=admin_menu_kb(pending_count=len(pending)))
    await call.answer()


@router.callback_query(F.data == "admin:requests")
async def admin_requests(call: CallbackQuery, db: Database, settings: Settings) -> None:
    tg = call.from_user
    if tg is None:
        return

    async with db.sessionmaker() as session:
        users = UserRepository(session)
        me = await users.get_by_telegram_id(tg.id)
        if me is None or not _is_admin(tg.id, me.is_admin, settings):
            await call.answer("Недостаточно прав", show_alert=True)
            return

        pending = await users.list_pending()

    if not pending:
        await call.message.edit_text("Заявок нет.", reply_markup=admin_menu_kb(pending_count=0))
        await call.answer()
        return

    await call.message.edit_text(
        f"Заявки на доступ: {len(pending)}\n"
        "Я отправлю вам сообщения с кнопками одобрения/отклонения для первых заявок."
    )
    from app.bot.keyboards import admin_request_kb

    for u in pending[:10]:
        await call.message.answer(
            f"@{u.username or '—'} (id={u.telegram_id})",
            reply_markup=admin_request_kb(u.telegram_id),
        )

    await call.answer()


@router.callback_query(F.data == "admin:users")
async def admin_users(call: CallbackQuery, db: Database, settings: Settings) -> None:
    tg = call.from_user
    if tg is None:
        return

    async with db.sessionmaker() as session:
        users = UserRepository(session)
        me = await users.get_by_telegram_id(tg.id)
        if me is None or not _is_admin(tg.id, me.is_admin, settings):
            await call.answer("Недостаточно прав", show_alert=True)
            return

        all_users = await users.list_all()

    await call.message.edit_text(
        f"Пользователи: {len(all_users)}\n"
        "Я отправлю карточки пользователей с кнопками управления (первые 15)."
    )

    for u in all_users[:15]:
        await call.message.answer(
            f"@{u.username or '—'} (id={u.telegram_id})\n"
            f"Статус: {u.status.value}",
            reply_markup=admin_user_kb(
                u.telegram_id,
                u.status.value,
                alerts=(u.alert_positions, u.alert_liquidations, u.alert_deposits, u.alert_withdrawals),
            ),
        )

    await call.answer()


@router.message(F.text.regexp(r"^/user\s+\d+$"))
async def admin_open_user(message: Message, db: Database, settings: Settings) -> None:
    tg = message.from_user
    if tg is None:
        return
    target_id = int((message.text or "").split()[-1])

    async with db.sessionmaker() as session:
        users = UserRepository(session)
        me = await users.get_by_telegram_id(tg.id)
        if me is None or not _is_admin(tg.id, me.is_admin, settings):
            await message.answer("Недостаточно прав.")
            return

        target = await users.get_by_telegram_id(target_id)
        if target is None:
            await message.answer("Пользователь не найден.")
            return

    await message.answer(
        f"Пользователь: @{target.username or '—'} (id={target.telegram_id})\n"
        f"Статус: {target.status.value}",
        reply_markup=admin_user_kb(
            target.telegram_id,
            target.status.value,
            alerts=(target.alert_positions, target.alert_liquidations, target.alert_deposits, target.alert_withdrawals),
        ),
    )


@router.callback_query(F.data.startswith("admin:approve:"))
async def admin_approve(call: CallbackQuery, db: Database, settings: Settings) -> None:
    await _admin_set_status(call, db, settings, UserStatus.approved)


@router.callback_query(F.data.startswith("admin:deny:"))
async def admin_deny(call: CallbackQuery, db: Database, settings: Settings) -> None:
    await _admin_set_status(call, db, settings, UserStatus.blocked)


@router.callback_query(F.data.startswith("admin:block:"))
async def admin_block(call: CallbackQuery, db: Database, settings: Settings) -> None:
    await _admin_set_status(call, db, settings, UserStatus.blocked)


async def _admin_set_status(call: CallbackQuery, db: Database, settings: Settings, status: UserStatus) -> None:
    tg = call.from_user
    if tg is None:
        return
    user_id = int(call.data.split(":")[-1])

    async with db.sessionmaker() as session:
        users = UserRepository(session)
        me = await users.get_by_telegram_id(tg.id)
        if me is None or not _is_admin(tg.id, me.is_admin, settings):
            await call.answer("Недостаточно прав", show_alert=True)
            return

        await users.set_status(user_id, status)
        await session.commit()

    try:
        if status == UserStatus.approved:
            await call.bot.send_message(chat_id=user_id, text="✅ Доступ к боту одобрен. Нажмите /menu")
        elif status == UserStatus.blocked:
            await call.bot.send_message(chat_id=user_id, text="⛔️ Доступ к боту отклонён/заблокирован.")
    except Exception:
        logger.debug("Could not notify user %s", user_id, exc_info=True)

    await call.answer("Готово")


@router.callback_query(F.data.startswith("admin:set_channel:"))
async def admin_set_channel_start(call: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    tg = call.from_user
    if tg is None:
        return

    target_id = int(call.data.split(":")[-1])
    async with db.sessionmaker() as session:
        users = UserRepository(session)
        me = await users.get_by_telegram_id(tg.id)
        if me is None or not _is_admin(tg.id, me.is_admin, settings):
            await call.answer("Недостаточно прав", show_alert=True)
            return

    await state.set_state(AdminStates.setting_channel)
    await state.update_data(target_id=target_id)
    await call.message.answer(
        "Отправьте chat_id канала/чата для доставки алертов этому пользователю.\n"
        "Чтобы вернуть доставку в ЛС — отправьте `dm`.",
        parse_mode="Markdown",
    )
    await call.answer()


@router.message(AdminStates.setting_channel, F.text)
async def admin_set_channel_text(message: Message, db: Database, settings: Settings, state: FSMContext) -> None:
    tg = message.from_user
    if tg is None:
        return

    data = await state.get_data()
    target_id = int(data.get("target_id"))
    text = (message.text or "").strip()

    async with db.sessionmaker() as session:
        users = UserRepository(session)
        me = await users.get_by_telegram_id(tg.id)
        if me is None or not _is_admin(tg.id, me.is_admin, settings):
            await message.answer("Недостаточно прав.")
            await state.clear()
            return

        if text.lower() == "dm":
            await users.set_delivery_channel(target_id, None)
            await session.commit()
            await message.answer("Ок. Доставка переведена в ЛС.")
            await state.clear()
            return

        # store as string (Telegram may use very large negative IDs)
        await users.set_delivery_channel(target_id, text)
        await session.commit()

    await message.answer(f"Ок. Доставка для пользователя {target_id} теперь в chat_id={text}")
    await state.clear()


@router.callback_query(F.data.startswith("admin:toggle:"))
async def admin_toggle_alert(call: CallbackQuery, db: Database, settings: Settings) -> None:
    tg = call.from_user
    if tg is None:
        return

    parts = (call.data or "").split(":")
    if len(parts) < 4:
        await call.answer("Некорректная команда", show_alert=True)
        return
    target_id = int(parts[2])
    category = parts[3]

    async with db.sessionmaker() as session:
        users = UserRepository(session)
        me = await users.get_by_telegram_id(tg.id)
        if me is None or not _is_admin(tg.id, me.is_admin, settings):
            await call.answer("Недостаточно прав", show_alert=True)
            return

        await users.toggle_alert(target_id, category)
        await session.commit()
        target = await users.get_by_telegram_id(target_id)

    if target is None:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    await call.message.edit_reply_markup(
        reply_markup=admin_user_kb(
            target.telegram_id,
            target.status.value,
            alerts=(target.alert_positions, target.alert_liquidations, target.alert_deposits, target.alert_withdrawals),
        )
    )
    await call.answer("Ок")


