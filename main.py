import logging
import re
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters
from config import TELEGRAM_BOT_TOKEN
from chat_handlers import handle_message, handle_start, handle_clear_command
from command_handlers import (
    handle_bots_list_command,
    handle_leaderboard_command,
    handle_leaderboard_reset_command,
    handle_whitelist_request_callback,
    handle_whitelist_approve_callback,
    handle_whitelist_list_command,
    handle_whitelist_remove_command,
    handle_economy_on_command,
    handle_economy_off_command,
    handle_collapsible_quote_on_command,
    handle_collapsible_quote_off_command,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("clear", handle_clear_command))
    app.add_handler(CommandHandler("leaderboard", handle_leaderboard_command))
    app.add_handler(CommandHandler("leaderboard_reset", handle_leaderboard_reset_command))
    app.add_handler(CommandHandler("whitelist_list", handle_whitelist_list_command))
    app.add_handler(CommandHandler("whitelist_remove", handle_whitelist_remove_command))
    app.add_handler(CommandHandler("economy_on", handle_economy_on_command))
    app.add_handler(CommandHandler("economy_off", handle_economy_off_command))
    app.add_handler(CommandHandler("collapsible_quote_on", handle_collapsible_quote_on_command))
    app.add_handler(CommandHandler("collapsible_quote_off", handle_collapsible_quote_off_command))
    app.add_handler(CallbackQueryHandler(handle_whitelist_approve_callback, pattern=re.compile(r"^whitelist_approve:")))
    app.add_handler(CallbackQueryHandler(handle_whitelist_request_callback, pattern=re.compile(r"^whitelist_request:")))
    app.add_handler(MessageHandler(filters.Regex(re.compile(r'^ии$', re.IGNORECASE)) & ~filters.COMMAND, handle_bots_list_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Остановка по Ctrl+C")