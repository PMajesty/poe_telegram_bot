import asyncio
import re
import aiohttp
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command, CommandObject
from config import POE_API_KEY, ADMIN_CHAT_ID, BOT_CONFIGS, ECONOMY_BOTS, ADMIN_USERNAME
from handlers_shared import db
import handlers_shared
from chat_handlers import safe_reply_markdown, ensure_whitelisted_or_prompt

router = Router()

def is_admin_user(user) -> bool:
    return bool(user and user.username == ADMIN_USERNAME)

async def fetch_current_balance(request_id: str = "N/A"):
    # [FIX] Added Accept-Encoding to avoid Brotli (br) compression issues
    headers = {
        "Authorization": f"Bearer {POE_API_KEY}",
        "Accept-Encoding": "gzip, deflate"
    }
    try:
        logging.info(f"[{request_id}] Requesting current balance from Poe API (async)...")
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.poe.com/usage/current_balance",
                headers=headers,
                timeout=10
            ) as resp:
                logging.info(f"[{request_id}] Current balance response status: {resp.status}")
                if resp.status != 200:
                    return None
                data = await resp.json()
                bal = data.get("current_point_balance")
                return int(bal) if bal is not None else None
    except Exception as e:
        logging.error(f"[{request_id}] Error fetching current balance: {e}")
        return None

@router.message(F.text.casefold() == "ии")
async def handle_bots_list_command_text(message: Message):
    await handle_bots_list_command(message)

async def handle_bots_list_command(message: Message):
    req_id = f"cmd_list_{message.message_id}"
    allowed, _ = await ensure_whitelisted_or_prompt(message)
    if not allowed:
        return
    sorted_bots = sorted(BOT_CONFIGS.items(), key=lambda item: item[1])

    if handlers_shared.economy_mode:
        reply_lines = ["*Доступные боты и их триггеры (включен режим экономии — доступны только экономичные боты для сохранения очков):*"]
        for triggers, model in sorted_bots:
            if model in ECONOMY_BOTS:
                safe_model = re.sub(r'([_*[]()~`>#+\-=|{}.!])', r'\\\1', model)
                trigger_str = ", ".join(f"`{t}`" for t in triggers)
                reply_lines.append(f"• *{safe_model}*: {trigger_str}")
    else:
        reply_lines = ["*Доступные боты и их триггеры:*"]
        for triggers, model in sorted_bots:
            safe_model = re.sub(r'([_*[]()~`>#+\-=|{}.!])', r'\\\1', model)
            trigger_str = ", ".join(f"`{t}`" for t in triggers)
            reply_lines.append(f"• *{safe_model}*: {trigger_str}")

    balance = await fetch_current_balance(req_id)
    reply_lines.append("")
    if balance is not None:
        reply_lines.append(f"Текущий баланс: {balance} очков")
    else:
        reply_lines.append("Не удалось получить текущий баланс очков.")
    reply_lines.append("")
    reply_lines.append("*Команды для всех пользователей:*")
    reply_lines.append("• `/start` — краткая справка и инструкция.")
    reply_lines.append("• `/clear <триггер>` — сбросить контекст выбранного бота.")
    reply_lines.append("• `/collapsible_quote_on` — включить режим разворачиваемых цитат в этом чате (ответы длиннее 500 символов будут отображаться в разворачиваемой цитате).")
    reply_lines.append("• `/collapsible_quote_off` — выключить режим разворачиваемых цитат в этом чате.")
    reply_lines.append("• Сообщение «ИИ» — показать список ботов, команд и текущий баланс.")
    reply_text = "\n".join(reply_lines)
    await safe_reply_markdown(message, reply_text, request_id=req_id)

@router.message(Command("leaderboard"))
async def handle_leaderboard_command(message: Message):
    req_id = f"cmd_lead_{message.message_id}"
    if not is_admin_user(message.from_user):
        return
    rows = await asyncio.to_thread(db.list_usage_leaderboard_usernames)
    if not rows:
        await message.reply("Нет данных по использованию.")
        return
    lines = ["Лидерборд по использованию очков:"]
    for idx, r in enumerate(rows, start=1):
        uname = r.get("username") or "нет данных"
        lines.append(f"{idx}. @{uname} — {r['total_points']} очков")
    text = "\n".join(lines)
    await safe_reply_markdown(message, text, request_id=req_id)

@router.message(Command("leaderboard_reset"))
async def handle_leaderboard_reset_command(message: Message):
    if not is_admin_user(message.from_user):
        return
    await asyncio.to_thread(db.reset_usage_leaderboard_usernames)
    await message.reply("Лидерборд сброшен.")

@router.callback_query(F.data.startswith("whitelist_request:"))
async def handle_whitelist_request_callback(callback: CallbackQuery):
    await callback.answer()
    data = callback.data or ""
    try:
        entity_id = int(data.split(":", 1)[1])
    except Exception:
        return
    try:
        await callback.message.edit_text("Ваш запрос был отправлен администратору.")
    except Exception:
        pass
    
    chat = callback.message.chat if callback.message else None
    user = callback.from_user
    if chat and chat.type == "private":
        name_or_tag = f"@{user.username}" if user and user.username else (user.full_name if user else "Unknown")
    else:
        name_or_tag = chat.title if chat and chat.title else str(entity_id)
        
    admin_text = f"Запрос на добавление в белый список.\nID: {entity_id}\nИмя/тег: {name_or_tag}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Добавить в белый список.", callback_data=f"whitelist_approve:{entity_id}")]
    ])
    try:
        logging.info(f"Sending whitelist request to admin chat {ADMIN_CHAT_ID}")
        await callback.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text, reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Failed to send whitelist request to admin: {e}")
        pass

@router.callback_query(F.data.startswith("whitelist_approve:"))
async def handle_whitelist_approve_callback(callback: CallbackQuery):
    await callback.answer()
    if not is_admin_user(callback.from_user):
        return
    data = callback.data or ""
    try:
        entity_id = int(data.split(":", 1)[1])
    except Exception:
        return
    await asyncio.to_thread(db.add_to_whitelist, entity_id)
    try:
        await callback.message.edit_text(f"ID {entity_id} добавлен в белый список.")
    except Exception:
        pass
    try:
        logging.info(f"Sending approval notification to {entity_id}")
        await callback.bot.send_message(chat_id=entity_id, text="Вы добавлены в белый список и можете пользоваться ботом.")
    except Exception as e:
        logging.error(f"Failed to send approval notification: {e}")
        pass

@router.message(Command("whitelist_list"))
async def handle_whitelist_list_command(message: Message):
    req_id = f"cmd_wlist_{message.message_id}"
    if not is_admin_user(message.from_user):
        return
    details = await asyncio.to_thread(db.list_whitelist_details)
    if not details:
        await message.reply("Белый список пуст.")
        return
    details = sorted(details, key=lambda d: (0 if d["last_username"] else 1))
    lines = ["Текущий белый список:"]
    msk = ZoneInfo("Europe/Moscow")
    for d in details:
        entity_id = d["entity_id"]
        last_username_val = d["last_username"]
        username_display = f"@{last_username_val}" if last_username_val else "нет данных"
        if d["last_activity"]:
            last_activity_display = (d["last_activity"].astimezone(msk) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        else:
            last_activity_display = "нет данных"
        lines.append(f"{username_display} | {entity_id} | Последняя активность: {last_activity_display}")
    text = "\n".join(lines)
    await safe_reply_markdown(message, text, request_id=req_id)

@router.message(Command("whitelist_remove"))
async def handle_whitelist_remove_command(message: Message, command: CommandObject):
    if not is_admin_user(message.from_user):
        return
    args = command.args
    if not args:
        await message.reply("Использование: /whitelist_remove <ID>")
        return
    try:
        entity_id = int(args.strip())
    except Exception:
        await message.reply("Неверный ID.")
        return
    await asyncio.to_thread(db.remove_from_whitelist, entity_id)
    await message.reply(f"ID {entity_id} удален из белого списка.")

@router.message(Command("economy_on"))
async def handle_economy_on_command(message: Message):
    if not is_admin_user(message.from_user):
        return
    handlers_shared.economy_mode = True
    await asyncio.to_thread(db.set_economy_mode, True)
    allowed_triggers = []
    for triggers, model in BOT_CONFIGS.items():
        if model in ECONOMY_BOTS:
            allowed_triggers.extend(triggers)
    allowed_triggers = list(dict.fromkeys(allowed_triggers))
    if allowed_triggers:
        await message.reply("Режим экономии включен. Доступны боты: " + ", ".join(allowed_triggers) + ".")
    else:
        await message.reply("Режим экономии включен.")

@router.message(Command("economy_off"))
async def handle_economy_off_command(message: Message):
    if not is_admin_user(message.from_user):
        return
    handlers_shared.economy_mode = False
    await asyncio.to_thread(db.set_economy_mode, False)
    await message.reply("Режим экономии выключен. Доступны все боты.")

@router.message(Command("collapsible_quote_on"))
async def handle_collapsible_quote_on_command(message: Message):
    allowed, _ = await ensure_whitelisted_or_prompt(message)
    if not allowed:
        return
    chat_id = message.chat.id
    await asyncio.to_thread(db.set_collapsible_quote_mode, chat_id, True)
    await message.reply("Режим разворачиваемых цитат включен для этого чата.")

@router.message(Command("collapsible_quote_off"))
async def handle_collapsible_quote_off_command(message: Message):
    allowed, _ = await ensure_whitelisted_or_prompt(message)
    if not allowed:
        return
    chat_id = message.chat.id
    await asyncio.to_thread(db.set_collapsible_quote_mode, chat_id, False)
    await message.reply("Режим разворачиваемых цитат выключен для этого чата.")