thoughts = """
I will update `connectivity_test.py` to route all requests through the specified proxy (`http://FFHNSC:XJ1r9Q@209.46.3.196:8000`).

This test will:
1.  Initialize an `aiogram.client.session.aiohttp.AiohttpSession` configured with the proxy.
2.  Instantiate the `Bot` using this session.
3.  Execute the `handle_bots_list_command` 20 times to verify that the specific payload (which previously triggered the network block) now passes successfully through the proxy.
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Message, Chat, User
from dotenv import load_dotenv
from command_handlers import handle_bots_list_command

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = "592350620"
ITERATIONS = 20
PROXY_URL = "http://FFHNSC:XJ1r9Q@209.46.3.196:8000"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

async def test_handle_bots_list_command_via_proxy():
    logging.info(f"--- STARTING PROXY CONNECTIVITY TEST ({ITERATIONS} iterations) ---")
    logging.info(f"Proxy: {PROXY_URL}")

    # Initialize session with Proxy and increased timeout
    session = AiohttpSession(
        proxy=PROXY_URL,
        timeout=60.0
    )

    bot = Bot(token=TOKEN, session=session)
    
    try:
        chat_id_int = int(CHAT_ID)
    except ValueError:
        logging.error(f"Invalid CHAT_ID: {CHAT_ID}")
        return

    test_user = User(id=chat_id_int, is_bot=False, first_name="ConnectivityTest", username="conn_test")
    test_chat = Chat(id=chat_id_int, type="private")

    success_count = 0
    start_time = time.time()

    try:
        for i in range(1, ITERATIONS + 1):
            logging.info(f"Iteration {i}/{ITERATIONS} starting...")
            try:
                message = Message(
                    message_id=i,
                    date=datetime.now(),
                    chat=test_chat,
                    from_user=test_user
                )
                message._bot = bot

                t0 = time.time()
                await handle_bots_list_command(message)
                dt = time.time() - t0
                
                logging.info(f"Req {i}/{ITERATIONS}: OK in {dt:.2f}s")
                success_count += 1
            except Exception as e:
                logging.error(f"Req {i}/{ITERATIONS}: FAILED: {type(e).__name__}: {e}")
            
            # Small delay to be polite, though the proxy should handle it
            await asyncio.sleep(0.5)
            
    finally:
        await session.close()

    total_duration = time.time() - start_time
    logging.info(f"Test Finished. Success: {success_count}/{ITERATIONS}. Total Duration: {total_duration:.2f}s")

async def main():
    if not TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN not found.")
        return

    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    await test_handle_bots_list_command_via_proxy()

if __name__ == "__main__":
    asyncio.run(main())