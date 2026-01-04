import asyncio
import re
import os
import logging
import io
import base64
import mimetypes
import requests
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

async def get_points_cost(query_id: str, created: int, bot_name: str) -> int | None:
    headers = {"Authorization": f"Bearer {POE_API_KEY}"}
    url = "https://api.poe.com/usage/points_history"
    
    logging.info(f"DEBUG: Fetching points for query_id={query_id}, created={created}, bot={bot_name}")

    for i in range(3):
        try:
            resp = await asyncio.to_thread(
                requests.get,
                url,
                headers=headers,
                params={"limit": 50},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                logging.info(f"DEBUG: API Response (Attempt {i+1}): {json.dumps(data)}")
                entries = data.get("data", [])
                
                if query_id:
                    for entry in entries:
                        e_id = entry.get("query_id")
                        if e_id == query_id:
                            logging.info(f"DEBUG: Found match by query_id: {entry.get('cost_points')}")
                            return entry.get("cost_points")
                
                if created:
                    for entry in entries:
                        entry_bot = entry.get("bot_name") or entry.get("app_name")
                        t = entry.get("creation_time", 0) / 1_000_000
                        diff = abs(t - created)
                        logging.info(f"DEBUG: Checking entry bot={entry_bot}, time={t}, diff={diff} vs target={created}")
                        if entry_bot == bot_name:
                            if diff < 5.0:
                                logging.info(f"DEBUG: Found match by time: {entry.get('cost_points')}")
                                return entry.get("cost_points")
            else:
                logging.warning(f"DEBUG: Failed to fetch points history: {resp.status_code} {resp.text}")
                
        except Exception as e:
            logging.error(f"DEBUG: Exception fetching points history: {e}")
        
        if i < 2:
            await asyncio.sleep(1)
            
    logging.info("DEBUG: No match found after retries.")
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

async def safe_reply_markdown(message: Message, text: str):
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
    
    for part in parts:
        max_retries = 10
        for attempt in range(max_retries):
            try:
                await message.answer(part, parse_mode=ParseMode.MARKDOWN_V2)
                break
            except TelegramRetryAfter as e:
                logging.warning(f"Flood limit exceeded. Waiting {e.retry_after} seconds.")
                await asyncio.sleep(e.retry_after)
                continue
            except TelegramNetworkError as e:
                wait_time = (attempt + 1) * 2
                logging.warning(f"Attempt {attempt + 1}/{max_retries} failed to send message: {e}. Retrying in {wait_time}s...")
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logging.exception("All retry attempts failed for message part.", exc_info=e)
                    try:
                        await message.answer("Error: Operation timed out or network error.")
                    except Exception:
                        pass
            except TelegramBadRequest as e:
                if "can't parse entities" in str(e).lower():
                    logging.warning("MarkdownV2 parse error, falling back to plain text: %s", e)
                    try:
                        plain = re.sub(r"\\([_*[]()~`>#+\-=|{}.!])", r"\1", part)
                        await message.answer(plain, parse_mode=None)
                    except Exception as e2:
                        logging.exception("Failed to send plain text fallback", exc_info=e2)
                        try:
                            await message.answer(
                                "Ошибка форматирования ответа, отправляю как обычный текст:\n\n" +
                                re.sub(r"\\([_*[]()~`>#+\-=|{}.!])", r"\1", part),
                                parse_mode=None
                            )
                        except Exception:
                            pass
                else:
                    logging.exception("BadRequest when sending message", exc_info=e)
                    try:
                        await message.answer(re.sub(r"\\([_*[]()~`>#+\-=|{}.!])", r"\1", part), parse_mode=None)
                    except Exception:
                        pass
                break
            except Exception as e:
                logging.exception("Unexpected error sending message", exc_info=e)
                try:
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
        await message.answer(
            "Вы не можете использовать данного бота, так как вашего аккаунта нет в белом списке.",
            reply_markup=keyboard
        )
        return False, entity_id
    return True, entity_id

@router.message(Command("start"))
async def handle_start(message: Message):
    await message.answer(
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
        await message.answer("Использование: /clear <триггер>")
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
        await message.answer("Неизвестный триггер или модель")
        return
    chat_id = message.chat.id
    await asyncio.to_thread(db.clear_context, chat_id, model)
    await message.answer(f"Контекст очищен для {model}")

@router.message(F.text | F.caption)
async def handle_message(message: Message):
    text = message.text or message.caption or ""
    trig, model, content = extract_trigger_and_text(text)
    if not trig or not model:
        return
    allowed, _ = await ensure_whitelisted_or_prompt(message)
    if not allowed:
        return
    chat_id = message.chat.id
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    
    if is_clear_command(content):
        await asyncio.to_thread(db.clear_context, chat_id, model)
        await message.answer(f"Контекст очищен для {model}")
        return

    if handlers_shared.economy_mode and model not in ECONOMY_BOTS:
        allowed_triggers = []
        for triggers, m in BOT_CONFIGS.items():
            if m in ECONOMY_BOTS:
                allowed_triggers.extend(triggers)
        allowed_triggers = list(dict.fromkeys(allowed_triggers))
        if allowed_triggers:
            await message.answer("Сейчас включен режим экономии очков. Доступны боты: " + ", ".join(allowed_triggers) + ". Пожалуйста, используйте один из них.")
        else:
            await message.answer("Сейчас включен режим экономии очков. Пожалуйста, используйте доступные боты.")
        return

    if not content and not (message.photo or message.video or message.document):
        await message.answer("Введите запрос после триггера или прикрепите файл.")
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
            await message.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
            
            file_info = await message.bot.get_file(attachment_source.file_id)
            file_content = io.BytesIO()
            await message.bot.download_file(file_info.file_path, file_content)
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
            logging.exception("Не удалось обработать вложение", exc_info=e)
            await message.answer("Не удалось обработать вложение.")
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
        await message.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        reply_data = await ai.chat(model, context_to_send)
    except Exception as e:
        logging.exception("Ошибка при обращении к модели %s", model, exc_info=e)
        await message.answer("Ошибка на стороне сервиса, попробуйте позже")
        return

    reply_text = reply_data.get("text", "")
    if reply_text.startswith("Generating..."):
        reply_text = reply_text[len("Generating..."):].lstrip()

    cleaned_reply = post_process_response_text(reply_text)
    normalized_reply = markdown_normalize(cleaned_reply)

    query_id = reply_data.get("id")
    created_time = reply_data.get("created")
    points_cost = await get_points_cost(query_id, created_time, model)
    
    decorated_reply = normalized_reply
    if points_cost is not None:
        user_username = message.from_user.username
        if user_username:
            await asyncio.to_thread(db.increment_usage_username, user_username, points_cost)
        decorated_reply = normalized_reply + f"\n\n**Стоимость {points_cost} очков**"
    else:
        decorated_reply = normalized_reply + "\n\n**Стоимость ?**"
    
    new_messages.append({"role": "assistant", "content": normalized_reply})
    trimmed = new_messages[-CONTEXT_MAX_MESSAGES:]
    
    trimmed_clean = []
    for m in trimmed:
        if isinstance(m, dict):
            m_copy = m.copy()
            m_copy.pop("attachments", None)
            trimmed_clean.append(m_copy)
        else:
            trimmed_clean.append(m)
            
    await asyncio.to_thread(db.set_context, chat_id, model, trimmed_clean)
    await asyncio.to_thread(db.append_log, chat_id, model, username, "user", content)
    await asyncio.to_thread(db.append_log, chat_id, model, username, "assistant", normalized_reply)
    
    await safe_reply_markdown(message, decorated_reply)