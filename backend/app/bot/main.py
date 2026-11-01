import asyncio
import logging

from aiogram import Bot, Dispatcher

from app.config import settings
from app.bot.handlers import router


async def main():

    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

    bot = Bot(token=settings.telegram_bot_token)

    dispatcher = Dispatcher()

    dispatcher.include_router(router)

    logging.info("Starting Telegram bot...")

    try:
        await bot.delete_webhook(drop_pending_updates=False)

        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types()
        )

    finally:
        await bot.session.close()


if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    asyncio.run(main())