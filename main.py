import asyncio
import contextlib
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher

from app.bot.routers import admin as admin_router
from app.bot.routers import user as user_router
from app.db.engine import Database
from app.hyperliquid.client import HyperliquidClient
from app.logging_config import setup_logging
from app.monitoring.monitor import TraderMonitor
from app.notify.telegram import TelegramNotifier
from app.notify.formatter import AlertFormatter
from settings import Settings

logger = logging.getLogger(__name__)


async def notify_admins_startup(bot: Bot, settings: Settings) -> None:
    """Notify admins that bot has started."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    message = (
        "🚀 <b>Бот запущен</b>\n\n"
        f"⏰ Время: <code>{timestamp}</code>\n"
        f"📊 Интервал опроса: {settings.hl_poll_interval_seconds}s\n"
        f"📝 Уровень логов: {settings.log_level}"
    )
    
    for admin_id in settings.bot_admins:
        try:
            await bot.send_message(chat_id=admin_id, text=message, parse_mode="HTML")
            logger.info(f"Startup notification sent to admin {admin_id}")
        except Exception as e:
            logger.warning(f"Failed to notify admin {admin_id}: {e}")


async def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, settings.log_dir, settings.max_log_files)

    db = Database(db_path=settings.db_path)
    await db.init()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp["db"] = db
    dp["settings"] = settings

    # Routers
    dp.include_router(user_router.router)
    dp.include_router(admin_router.router)

    # Background monitoring
    hl = HyperliquidClient()
    dp["hl"] = hl
    notifier = TelegramNotifier(bot=bot, db=db)
    formatter = AlertFormatter()
    monitor = TraderMonitor(
        db=db,
        hl=hl,
        notifier=notifier,
        formatter=formatter,
        poll_interval_seconds=settings.hl_poll_interval_seconds,
    )

    # Notify admins about startup
    await notify_admins_startup(bot, settings)

    monitor_task = asyncio.create_task(monitor.run_forever(), name="hyperliquid-monitor")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        monitor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await monitor_task
        await hl.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())


