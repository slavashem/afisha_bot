import asyncio
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import load_config
from database.db import init_db
from handlers.admin import router as admin_router, register_admin_handlers, check_and_notify
from handlers.publish import router as publish_router, register_publish_handlers
from services.scheduler import create_scheduler, schedule_check
from utils.logger import logger

os.makedirs("data", exist_ok=True)


async def main() -> None:
    config = load_config()
    await init_db(config.db_path)

    bot = Bot(token=config.telegram_bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    register_admin_handlers(admin_router, bot, config)
    register_publish_handlers(publish_router, bot, config)

    dp.include_router(admin_router)
    dp.include_router(publish_router)

    scheduler = create_scheduler()
    schedule_check(
        scheduler,
        job_func=lambda: asyncio.create_task(check_and_notify(bot, config)),
        interval_minutes=config.check_interval_minutes,
    )
    scheduler.start()
    logger.info("Bot started")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
