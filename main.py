import logging
import os
import asyncio
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.client.session.aiohttp import AiohttpSession
from config import TELEGRAM_BOT_TOKEN, UPLOAD_PROXY_URL
from command_handlers import router as command_router
from chat_handlers import router as chat_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

async def main():
    current_no_proxy = os.environ.get("NO_PROXY", "")
    if "api.telegram.org" not in current_no_proxy:
        os.environ["NO_PROXY"] = ",".join(filter(None, [current_no_proxy, "api.telegram.org"]))

    session = AiohttpSession(
        proxy=UPLOAD_PROXY_URL,
        timeout=60.0
    )

    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
        session=session
    )
    
    dp = Dispatcher()
    
    dp.include_router(command_router)
    dp.include_router(chat_router)

    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Stopped by Ctrl+C")