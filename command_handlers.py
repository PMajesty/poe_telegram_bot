import asyncio
import re
import requests
from datetime import timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import POE_API_KEY, ADMIN_CHAT_ID, BOT_CONFIGS, ECONOMY_BOTS, ADMIN_USERNAME
from handlers_shared import db
import handlers_shared
from chat_handlers import safe_reply_markdown, ensure_whitelisted_or_prompt

def is_admin_user(user) -> bool:
    return bool(user and user.username == ADMIN_USERNAME)

async def fetch_current_balance():
    headers = {"Authorization": f"Bearer {POE_API_KEY}"}
    try:
        resp = await asyncio.to_thread(
            requests.get,
            "https://api.poe.com/usage/current_balance",
            headers=headers,
            timeout=10
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        bal = data.get("current_point_balance")
        return int(bal) if bal is not None else None
    except Exception:
        return None

def build_trigger_map():
    m = {}
    for triggers, model in BOT_CONFIGS.items():
        for t in triggers:
            m[t.lower()] = model
    return m

async def handle_bots_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    allowed, _ = await ensure_whitelisted_or_prompt(update, context)
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

    balance = await fetch_current_balance()
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
    await safe_reply_markdown(update, reply_text)

async def handle_leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not is_admin_user(update.effective_user):
        return
    rows = await asyncio.to_thread(db.list_usage_leaderboard_usernames)
    if not rows:
        await update.message.reply_text("Нет данных по использованию.")
        return
    lines = ["Лидерборд по использованию очков:"]
    for idx, r in enumerate(rows, start=1):
        uname = r.get("username") or "нет данных"
        lines.append(f"{idx}. @{uname} — {r['total_points']} очков")
    text = "\n".join(lines)
    await safe_reply_markdown(update, text)

async def handle_leaderboard_reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not is_admin_user(update.effective_user):
        return
    await asyncio.to_thread(db.reset_usage_leaderboard_usernames)
    await update.message.reply_text("Лидерборд сброшен.")

async def handle_whitelist_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if not data.startswith("whitelist_request:"):
        return
    try:
        entity_id = int(data.split(":", 1)[1])
    except Exception:
        return
    try:
        await query.edit_message_text("Ваш запрос был отправлен администратору.")
    except Exception:
        pass
    chat = query.message.chat if query.message else update.effective_chat
    user = query.from_user
    if chat and chat.type == "private":
        name_or_tag = f"@{user.username}" if user and user.username else (user.full_name if user else "Unknown")
    else:
        name_or_tag = chat.title if chat and chat.title else str(entity_id)
    admin_text = f"Запрос на добавление в белый список.\nID: {entity_id}\nИмя/тег: {name_or_tag}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Добавить в белый список.", callback_data=f"whitelist_approve:{entity_id}")]
    ])
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text, reply_markup=keyboard)
    except Exception:
        pass

async def handle_whitelist_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_admin_user(query.from_user):
        return
    data = query.data or ""
    if not data.startswith("whitelist_approve:"):
        return
    try:
        entity_id = int(data.split(":", 1)[1])
    except Exception:
        return
    await asyncio.to_thread(db.add_to_whitelist, entity_id)
    try:
        await query.edit_message_text(f"ID {entity_id} добавлен в белый список.")
    except Exception:
        pass
    try:
        await context.bot.send_message(chat_id=entity_id, text="Вы добавлены в белый список и можете пользоваться ботом.")
    except Exception:
        pass

async def handle_whitelist_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not is_admin_user(update.effective_user):
        return
    details = await asyncio.to_thread(db.list_whitelist_details)
    if not details:
        await update.message.reply_text("Белый список пуст.")
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
    await safe_reply_markdown(update, text)

async def handle_whitelist_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not is_admin_user(update.effective_user):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /whitelist_remove <ID>")
        return
    try:
        entity_id = int(args[0])
    except Exception:
        await update.message.reply_text("Неверный ID.")
        return
    await asyncio.to_thread(db.remove_from_whitelist, entity_id)
    await update.message.reply_text(f"ID {entity_id} удален из белого списка.")

async def handle_economy_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not is_admin_user(update.effective_user):
        return
    handlers_shared.economy_mode = True
    await asyncio.to_thread(db.set_economy_mode, True)
    allowed_triggers = []
    for triggers, model in BOT_CONFIGS.items():
        if model in ECONOMY_BOTS:
            allowed_triggers.extend(triggers)
    allowed_triggers = list(dict.fromkeys(allowed_triggers))
    if allowed_triggers:
        await update.message.reply_text("Режим экономии включен. Доступны боты: " + ", ".join(allowed_triggers) + ".")
    else:
        await update.message.reply_text("Режим экономии включен.")

async def handle_economy_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not is_admin_user(update.effective_user):
        return
    handlers_shared.economy_mode = False
    await asyncio.to_thread(db.set_economy_mode, False)
    await update.message.reply_text("Режим экономии выключен. Доступны все боты.")

async def handle_collapsible_quote_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    allowed, _ = await ensure_whitelisted_or_prompt(update, context)
    if not allowed:
        return
    chat_id = update.effective_chat.id
    await asyncio.to_thread(db.set_collapsible_quote_mode, chat_id, True)
    await update.message.reply_text("Режим разворачиваемых цитат включен для этого чата.")

async def handle_collapsible_quote_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    allowed, _ = await ensure_whitelisted_or_prompt(update, context)
    if not allowed:
        return
    chat_id = update.effective_chat.id
    await asyncio.to_thread(db.set_collapsible_quote_mode, chat_id, False)
    await update.message.reply_text("Режим разворачиваемых цитат выключен для этого чата.")