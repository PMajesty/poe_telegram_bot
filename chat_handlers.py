import asyncio
import re
import os
import logging
import io
import base64
import mimetypes
import aiohttp
import json
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramBadRequest
import telegramify_markdown
from config import BOT_CONFIGS, CONTEXT_MAX_MESSAGES, POE_API_KEY, ADMIN_CHAT_ID, ECONOMY_BOTS, UPLOAD_PROXY_URL
from handlers_shared import db
import handlers_shared
from ai_client import PoeChatClient
from utils import post_process_response_text, markdown_normalize, sanitize_and_chunk_text, chunk_text
from aiogram.filters import Command, CommandObject

router = Router()
ai = PoeChatClient()

async def get_points_cost(query_id: str, created: int, bot_name: str, request_id: str = "N/A") -> int | None:
    headers = {
        "Authorization": f"Bearer {POE_API_KEY}",
        "Accept-Encoding": "gzip, deflate"
    }
    url = "https://api.poe.com/usage/points_history"
    
    for i in range(3):
        try:
            logging.info(f"[{request_id}] Fetching points history (attempt {i+1}) for query_id={query_id} (async)...")
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    params={"limit": 50},
                    timeout=10
                ) as resp:
                    logging.info(f"[{request_id}] Points history response status: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        entries = data.get("data", [])
                        
                        if query_id:
                            for entry in entries:
                                e_id = entry.get("query_id")
                                if e_id == query_id:
                                    return entry.get("cost_points")
                        
                        if created:
                            for entry in entries:
                                entry_bot = entry.get("bot_name") or entry.get("app_name")
                                t = entry.get("creation_time", 0) / 1_000_000
                                diff = abs(t - created)
                                if entry_bot == bot_name:
                                    if diff < 5.0:
                                        return entry.get("cost_points")
                    else:
                        pass
                
        except Exception as e:
            logging.error(f"[{request_id}] Error fetching points cost: {e}")
            pass
        
        if i < 2:
            await asyncio.sleep(1)
            
    return None

def build_trigger_map():
    m = {}
    for triggers, model in BOT_CONFIGS.items():
        for t in triggers:
            m[t.lower()] = model
    return m

def sorted_triggers():
    t = list(build_trigger_map().keys())
    t.sort(key=len, reverse=True)
    return t

def extract_trigger_and_text(text):
    if not text:
        return None, None, None
    tmap = build_trigger_map()
    lower = text.lower()
    for trig in sorted_triggers():
        if not lower.startswith(trig):
            continue
        if len(lower) == len(trig):
            content = text[len(trig):].strip()
            return trig, tmap[trig], content
        nxt = lower[len(trig)]
        if nxt.isalnum():
            continue
        content = text[len(trig):].lstrip(" \t,.:;|/-----")
        return trig, tmap[trig], content
    return None, None, None

def is_clear_command(s):
    if not s:
        return False
    x = s.strip().lower()
    x = re.sub(r"^[,.\s]+|[,.\s]+$", "", x)
    return x in {"clear context", "clear", "reset", "очистить контекст", "очистить", "сброс"}

async def safe_reply_markdown(message: Message, text: str, request_id: str = "N/A"):
    if not text:
        return
    chat_id = message.chat.id
    use_collapsible_quote = False
    if chat_id is not None:
        try:
            use_collapsible_quote = await asyncio.to_thread(db.get_collapsible_quote_mode, chat_id)
        except Exception:
            use_collapsible_quote = False
    
    if use_collapsible_quote and len(text) > 500:
        sanitized = telegramify_markdown.markdownify(
            text,
            max_line_length=None,
            normalize_whitespace=False
        )
        sanitized = sanitized.replace("```", "")
        lines = sanitized.split("\n")
        quoted_lines = []
        for i, l in enumerate(lines):
            prefix = "**>" if i == 0 else ">"
            quoted_lines.append(prefix + (l if l.strip() != "" else ""))
        quoted = "\n".join(quoted_lines)
        parts = chunk_text(quoted, limit=4000)
        parts = [p + "||" for p in parts]
    else:
        parts = sanitize_and_chunk_text(text)
    
    for i, part in enumerate(parts):
        max_retries = 10
        for attempt in range(max_retries):
            try:
                logging.info(f"[{request_id}] Sending message part {i+1}/{len(parts)} to chat {chat_id} (attempt {attempt+1})...")
                if i == 0:
                    await message.reply(part, parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await message.answer(part, parse_mode=ParseMode.MARKDOWN_V2)
                logging.info(f"[{request_id}] Message part {i+1} sent successfully.")
                break
            except TelegramRetryAfter as e:
                logging.warning(f"[{request_id}] Flood limit exceeded. Waiting {e.retry_after} seconds.")
                await asyncio.sleep(e.retry_after)
                continue
            except TelegramNetworkError as e:
                wait_time = (attempt + 1) * 2
                logging.warning(f"[{request_id}] Attempt {attempt + 1}/{max_retries} failed to send message: {e}. Retrying in {wait_time}s...")
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logging.exception(f"[{request_id}] All retry attempts failed for message part.", exc_info=e)
                    try:
                        if i == 0:
                            await message.reply("Error: Operation timed out or network error.")
                        else:
                            await message.answer("Error: Operation timed out or network error.")
                    except Exception:
                        pass
            except TelegramBadRequest as e:
                if "can't parse entities" in str(e).lower():
                    logging.warning(f"[{request_id}] MarkdownV2 parse error, falling back to plain text: %s", e)
                    try:
                        plain = re.sub(r"\\([_*[]()~`>#+\-=|{}.!])", r"\1", part)
                        logging.info(f"[{request_id}] Sending fallback plain text message...")
                        if i == 0:
                            await message.reply(plain, parse_mode=None)
                        else:
                            await message.answer(plain, parse_mode=None)
                        logging.info(f"[{request_id}] Fallback message sent.")
                    except Exception as e2:
                        logging.exception(f"[{request_id}] Failed to send plain text fallback", exc_info=e2)
                        try:
                            fallback_text = "Ошибка форматирования ответа, отправляю как обычный текст:\n\n" + re.sub(r"\\([_*[]()~`>#+\-=|{}.!])", r"\1", part)
                            if i == 0:
                                await message.reply(fallback_text, parse_mode=None)
                            else:
                                await message.answer(fallback_text, parse_mode=None)
                        except Exception:
                            pass
                else:
                    logging.exception(f"[{request_id}] BadRequest when sending message", exc_info=e)
                    try:
                        logging.info(f"[{request_id}] Sending sanitized fallback message...")
                        sanitized_text = re.sub(r"\\([_*[]()~`>#+\-=|{}.!])", r"\1", part)
                        if i == 0:
                            await message.reply(sanitized_text, parse_mode=None)
                        else:
                            await message.answer(sanitized_text, parse_mode=None)
                        logging.info(f"[{request_id}] Fallback message sent.")
                    except Exception:
                        pass
                break
            except Exception as e:
                logging.exception(f"[{request_id}] Unexpected error sending message", exc_info=e)
                try:
                    if i == 0:
                        await message.reply(f"Error: {e}")
                    else:
                        await message.answer(f"Error: {e}")
                except Exception:
                    pass
                break

async def ensure_whitelisted_or_prompt(message: Message):
    chat = message.chat
    user = message.from_user
    if chat.type == "private":
        entity_id = user.id
    else:
        entity_id = chat.id

    allowed = await asyncio.to_thread(db.is_whitelisted, entity_id)
    if not allowed:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отправить запрос на добавление в белый список.", callback_data=f"whitelist_request:{entity_id}")]
        ])
        await message.reply(
            "Вы не можете использовать данного бота, так как вашего аккаунта нет в белом списке.",
            reply_markup=keyboard
        )
        return False, entity_id
    return True, entity_id

@router.message(Command("start"))
async def handle_start(message: Message):
    await message.reply(
        "Отправьте сообщение, начиная с триггера, например: gpt В чем смысл жизни?\n"
        "Используйте: /clear <триггер>, чтобы сбросить контекст"
    )

@router.message(Command("clear"))
async def handle_clear_command(message: Message, command: CommandObject):
    allowed, _ = await ensure_whitelisted_or_prompt(message)
    if not allowed:
        return
    args = command.args
    if not args:
        await message.reply("Использование: /clear <триггер>")
        return
    tmap = build_trigger_map()
    trig = args.split()[0].lower()
    model = None
    if trig in tmap:
        model = tmap[trig]
    else:
        for k, v in tmap.items():
            if v.lower() == trig:
                model = v
                break
    if not model:
        await message.reply("Неизвестный триггер или модель")
        return
    chat_id = message.chat.id
    await asyncio.to_thread(db.clear_context, chat_id, model)
    await message.reply(f"Контекст очищен для {model}")

@router.message(F.text | F.caption)
async def handle_message(message: Message):
    req_id = f"msg_{message.message_id}"
    text = message.text or message.caption or ""
    trig, model, content = extract_trigger_and_text(text)
    if not trig or not model:
        return
    allowed, _ = await ensure_whitelisted_or_prompt(message)
    if not allowed:
        return
    chat_id = message.chat.id
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    
    logging.info(f"[{req_id}] Handling message from {username} (chat {chat_id}), model: {model}")

    if is_clear_command(content):
        await asyncio.to_thread(db.clear_context, chat_id, model)
        await message.reply(f"Контекст очищен для {model}")
        return

    if handlers_shared.economy_mode and model not in ECONOMY_BOTS:
        allowed_triggers = []
        for triggers, m in BOT_CONFIGS.items():
            if m in ECONOMY_BOTS:
                allowed_triggers.extend(triggers)
        allowed_triggers = list(dict.fromkeys(allowed_triggers))
        if allowed_triggers:
            await message.reply("Сейчас включен режим экономии очков. Доступны боты: " + ", ".join(allowed_triggers) + ". Пожалуйста, используйте один из них.")
        else:
            await message.reply("Сейчас включен режим экономии очков. Пожалуйста, используйте доступные боты.")
        return

    if not content and not (message.photo or message.video or message.document):
        await message.reply("Введите запрос после триггера или прикрепите файл.")
        return

    attachments = []
    attachment_source = None
    if message.photo:
        attachment_source = message.photo[-1]
    elif message.video:
        attachment_source = message.video
    elif message.document:
        attachment_source = message.document

    if attachment_source:
        try:
            try:
                logging.info(f"[{req_id}] Sending ChatAction.UPLOAD_DOCUMENT...")
                await message.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
            except Exception as e:
                logging.warning(f"[{req_id}] Failed to send ChatAction.UPLOAD_DOCUMENT: {e}")

            logging.info(f"[{req_id}] Downloading file from Telegram: {attachment_source.file_id}")
            file_info = await message.bot.get_file(attachment_source.file_id)
            file_content = io.BytesIO()
            await message.bot.download_file(file_info.file_path, file_content)
            logging.info(f"[{req_id}] File downloaded successfully.")
            file_content.seek(0)
            file_bytes = file_content.read()
            
            file_name = getattr(attachment_source, 'file_name', None) or 'attachment.dat'
            
            mime_type = "application/octet-stream"
            if hasattr(attachment_source, 'mime_type') and attachment_source.mime_type:
                mime_type = attachment_source.mime_type
            else:
                guessed, _ = mimetypes.guess_type(file_name)
                if guessed:
                    mime_type = guessed
                elif message.photo:
                    mime_type = "image/jpeg"
                elif message.video:
                    mime_type = "video/mp4"

            b64_data = base64.b64encode(file_bytes).decode('utf-8')
            
            attachments.append({
                "filename": file_name,
                "content_type": mime_type,
                "data_base64": b64_data
            })

        except Exception as e:
            logging.exception(f"[{req_id}] Не удалось обработать вложение", exc_info=e)
            await message.reply("Не удалось обработать вложение.")
            return

    old_messages = await asyncio.to_thread(db.get_context, chat_id, model)
    new_messages = list(old_messages)
    
    user_message = {"role": "user", "content": content}
    if attachments:
        user_message["attachments"] = attachments
    
    new_messages.append(user_message)

    context_to_send = new_messages[-CONTEXT_MAX_MESSAGES:]
    
    reply_data = {}
    try:
        try:
            logging.info(f"[{req_id}] Sending ChatAction.TYPING...")
            await message.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception as e:
            logging.warning(f"[{req_id}] Failed to send ChatAction.TYPING: {e}")
        
        reply_data = await ai.chat(model, context_to_send, request_id=req_id)
    except Exception as e:
        logging.exception(f"[{req_id}] Ошибка при обращении к модели %s", model, exc_info=e)
        await message.reply("Ошибка на стороне сервиса, попробуйте позже")
        return

    reply_text = reply_data.get("text", "")
    if reply_text.startswith("Generating..."):
        reply_text = reply_text[len("Generating..."):].lstrip()

    cleaned_reply = post_process_response_text(reply_text)
    normalized_reply = markdown_normalize(cleaned_reply)

    query_id = reply_data.get("id")
    created_time = reply_data.get("created")
    points_cost = await get_points_cost(query_id, created_time, model, request_id=req_id)
    
    decorated_reply = normalized_reply
    if points_cost is not None:
        user_username = message.from_user.username
        if user_username:
            await asyncio.to_thread(db.increment_usage_username, user_username, points_cost)
        decorated_reply = normalized_reply + f"\n\n**Стоимость {points_cost} очков**"
    else:
        decorated_reply = normalized_reply + "\n\n**Стоимость ?**"
    
    latest_messages = await asyncio.to_thread(db.get_context, chat_id, model)
    
    final_user_content = content
    final_assistant_content = normalized_reply

    if latest_messages != old_messages:
        final_user_content += "\n\n[THIS QUERY HAS BEEN SIMULTANEOUS, CHRONOLOGICAL ERRORS POSSIBLE]"
        final_assistant_content += "\n\n[THIS RESPONSE HAS BEEN SIMULTANEOUS, CHRONOLOGICAL ERRORS POSSIBLE]"
    
    user_msg_to_save = {"role": "user", "content": final_user_content}
    
    assistant_msg_to_save = {"role": "assistant", "content": final_assistant_content}
    
    latest_messages.append(user_msg_to_save)
    latest_messages.append(assistant_msg_to_save)
    
    trimmed = latest_messages[-CONTEXT_MAX_MESSAGES:]
    
    trimmed_clean = []
    for m in trimmed:
        if isinstance(m, dict):
            m_copy = m.copy()
            m_copy.pop("attachments", None)
            trimmed_clean.append(m_copy)
        else:
            trimmed_clean.append(m)
            
    await asyncio.to_thread(db.set_context, chat_id, model, trimmed_clean)
    await asyncio.to_thread(db.append_log, chat_id, model, username, "user", final_user_content)
    await asyncio.to_thread(db.append_log, chat_id, model, username, "assistant", final_assistant_content)
    
    await safe_reply_markdown(message, decorated_reply, request_id=req_id)