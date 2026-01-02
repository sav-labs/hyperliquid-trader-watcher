import asyncio
import contextlib

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


