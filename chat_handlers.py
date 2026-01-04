import asyncio
import re
import os
import logging
import fastapi_poe as fp
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest, TimedOut, NetworkError
import telegramify_markdown
from config import BOT_CONFIGS, CONTEXT_MAX_MESSAGES, POE_API_KEY, ADMIN_CHAT_ID, ECONOMY_BOTS, UPLOAD_PROXY_URL
from handlers_shared import db
import handlers_shared
from ai_client import PoeChatClient
from utils import post_process_response_text, markdown_normalize, sanitize_and_chunk_text, chunk_text, sanitize_markdown_v2

ai = PoeChatClient()

async def fetch_last_request_cost():
    headers = {"Authorization": f"Bearer {POE_API_KEY}"}
    try:
        resp = await asyncio.to_thread(
            requests.get,
            "https://api.poe.com/usage/points_history",
            headers=headers,
            params={"limit": 1},
            timeout=10
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        entries = data.get("data") or []
        if not entries:
            return None
        cost = entries[0].get("cost_points")
        try:
            return int(cost) if cost is not None else None
        except Exception:
            return None
    except Exception:
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

async def safe_reply_markdown(update: Update, text: str):
    if not text:
        return
    chat_id = update.effective_chat.id if update.effective_chat else None
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
        max_retries = 5
        backoff = 1.0
        for attempt in range(max_retries):
            try:
                await update.message.reply_text(part, parse_mode="MarkdownV2")
                break
            except (TimedOut, NetworkError) as e:
                logging.warning(f"Attempt {attempt + 1}/{max_retries} failed to send message: {e}. Retrying in {backoff}s...")
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                else:
                    logging.exception("All retry attempts failed for message part.", exc_info=e)
                    try:
                        await update.message.reply_text("Error: Operation timed out or network error.")
                    except Exception:
                        pass
            except BadRequest as e:
                if "Can't parse entities" in str(e):
                    logging.warning("MarkdownV2 parse error, falling back to plain text: %s", e)
                    try:
                        plain = re.sub(r"\\([_*[]()~`>#+\-=|{}.!])", r"\1", part)
                        await update.message.reply_text(plain, parse_mode=None)
                    except Exception as e2:
                        logging.exception("Failed to send plain text fallback", exc_info=e2)
                        try:
                            await update.message.reply_text(
                                "Ошибка форматирования ответа, отправляю как обычный текст:\n\n" +
                                re.sub(r"\\([_*[]()~`>#+\-=|{}.!])", r"\1", part),
                                parse_mode=None
                            )
                        except Exception:
                            pass
                else:
                    logging.exception("BadRequest when sending message", exc_info=e)
                    try:
                        await update.message.reply_text(re.sub(r"\\([_*[]()~`>#+\-=|{}.!])", r"\1", part), parse_mode=None)
                    except Exception:
                        pass
                break
            except Exception as e:
                logging.exception("Unexpected error sending message", exc_info=e)
                try:
                    await update.message.reply_text(f"Error: {e}")
                except Exception:
                    pass
                break

async def ensure_whitelisted_or_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        entity_id = user.id
    else:
        entity_id = chat.id

    allowed = await asyncio.to_thread(db.is_whitelisted, entity_id)
    if not allowed:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Отправить запрос на добавление в белый список.", callback_data=f"whitelist_request:{entity_id}")]
        ])
        await update.message.reply_text(
            "Вы не можете использовать данного бота, так как вашего аккаунта нет в белом списке.",
            reply_markup=keyboard
        )
        return False, entity_id
    return True, entity_id

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = update.message.text or update.message.caption or ""
    trig, model, content = extract_trigger_and_text(text)
    if not trig or not model:
        return
    allowed, _ = await ensure_whitelisted_or_prompt(update, context)
    if not allowed:
        return
    chat_id = update.effective_chat.id
    username = update.message.from_user.username or update.message.from_user.first_name or "Unknown"
    if is_clear_command(content):
        await asyncio.to_thread(db.clear_context, chat_id, model)
        await update.message.reply_text(f"Контекст очищен для {model}")
        return

    if handlers_shared.economy_mode and model not in ECONOMY_BOTS:
        allowed_triggers = []
        for triggers, m in BOT_CONFIGS.items():
            if m in ECONOMY_BOTS:
                allowed_triggers.extend(triggers)
        allowed_triggers = list(dict.fromkeys(allowed_triggers))
        if allowed_triggers:
            await update.message.reply_text("Сейчас включен режим экономии очков. Доступны боты: " + ", ".join(allowed_triggers) + ". Пожалуйста, используйте один из них.")
        else:
            await update.message.reply_text("Сейчас включен режим экономии очков. Пожалуйста, используйте доступные боты.")
        return

    if not content and not (update.message.photo or update.message.video or update.message.document):
        await update.message.reply_text("Введите запрос после триггера или прикрепите файл.")
        return

    attachments = []
    attachment_source = None
    if update.message.photo:
        attachment_source = update.message.photo[-1]
    elif update.message.video:
        attachment_source = update.message.video
    elif update.message.document:
        attachment_source = update.message.document

    if attachment_source:
        old_http = os.environ.get("HTTP_PROXY")
        old_https = os.environ.get("HTTPS_PROXY")
        old_all = os.environ.get("ALL_PROXY")
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action='upload_document')
            tg_file = await attachment_source.get_file()
            file_content = await tg_file.download_as_bytearray()
            file_name = getattr(attachment_source, 'file_name', None) or 'attachment.dat'

            proxy_url = UPLOAD_PROXY_URL
            if proxy_url:
                os.environ["HTTP_PROXY"] = proxy_url
                os.environ["HTTPS_PROXY"] = proxy_url
                os.environ["ALL_PROXY"] = proxy_url

            poe_attachment = await fp.upload_file(
                file=bytes(file_content),
                file_name=file_name,
                api_key=POE_API_KEY
            )
            attachments.append(poe_attachment)
        except Exception as e:
            logging.exception("Не удалось обработать вложение", exc_info=e)
            await update.message.reply_text("Не удалось обработать вложение.")
            return
        finally:
            if old_http is None:
                os.environ.pop("HTTP_PROXY", None)
            else:
                os.environ["HTTP_PROXY"] = old_http
            if old_https is None:
                os.environ.pop("HTTPS_PROXY", None)
            else:
                os.environ["HTTPS_PROXY"] = old_https
            if old_all is None:
                os.environ.pop("ALL_PROXY", None)
            else:
                os.environ["ALL_PROXY"] = old_all

    if content:
        web_search_params = {"web_search": "true"}
    else:
        web_search_params = None

    old_messages = await asyncio.to_thread(db.get_context, chat_id, model)
    new_messages = list(old_messages)
    user_message = {"role": "user", "content": content}
    if attachments:
        user_message["attachments"] = attachments
    if web_search_params:
        user_message["parameters"] = web_search_params
    new_messages.append(user_message)

    context_to_send = new_messages[-CONTEXT_MAX_MESSAGES:]
    if context_to_send:
        sanitized_context = []
        for i, m in enumerate(context_to_send):
            if isinstance(m, dict) and i < len(context_to_send) - 1 and "parameters" in m:
                m_copy = m.copy()
                m_copy.pop("parameters", None)
                sanitized_context.append(m_copy)
            else:
                sanitized_context.append(m)
        context_to_send = sanitized_context
    
    reply_data = {}
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')
        reply_data = await ai.chat(model, context_to_send)
    except Exception as e:
        logging.exception("Ошибка при обращении к модели %s", model, exc_info=e)
        await update.message.reply_text("Ошибка на стороне сервиса, попробуйте позже")
        return

    reply_text = reply_data.get("text", "")
    if reply_text.startswith("Generating..."):
        reply_text = reply_text[len("Generating..."):].lstrip()

    response_attachments = reply_data.get("attachments", [])

    if response_attachments:
        for attachment in response_attachments:
            if attachment.inline_ref and attachment.url:
                pattern = re.compile(r"![.*?][" + re.escape(attachment.inline_ref) + r"]")
                image_markdown_link = f"[\u200b]({attachment.url})"
                reply_text, _ = re.subn(pattern, image_markdown_link, reply_text)

    cleaned_reply = post_process_response_text(reply_text)
    normalized_reply = markdown_normalize(cleaned_reply)

    cost_points = await fetch_last_request_cost()
    decorated_reply = normalized_reply
    if cost_points is not None:
        user_username = update.message.from_user.username
        if user_username:
            await asyncio.to_thread(db.increment_usage_username, user_username, cost_points)
        decorated_reply = normalized_reply + f"\n\n**Стоимость {cost_points} очков**"
    
    new_messages.append({"role": "assistant", "content": normalized_reply})
    trimmed = new_messages[-CONTEXT_MAX_MESSAGES:]
    
    trimmed_no_attachments = []
    for m in trimmed:
        if isinstance(m, dict) and ("attachments" in m or "parameters" in m):
            m_copy = m.copy()
            m_copy.pop("attachments", None)
            m_copy.pop("parameters", None)
            trimmed_no_attachments.append(m_copy)
        else:
            trimmed_no_attachments.append(m)
            
    await asyncio.to_thread(db.set_context, chat_id, model, trimmed_no_attachments)
    await asyncio.to_thread(db.append_log, chat_id, model, username, "user", content)
    await asyncio.to_thread(db.append_log, chat_id, model, username, "assistant", normalized_reply)
    
    await safe_reply_markdown(update, decorated_reply)

async def handle_clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    allowed, _ = await ensure_whitelisted_or_prompt(update, context)
    if not allowed:
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /clear <триггер>")
        return
    tmap = build_trigger_map()
    trig = args[0].lower()
    model = None
    if trig in tmap:
        model = tmap[trig]
    else:
        for k, v in tmap.items():
            if v.lower() == trig:
                model = v
                break
    if not model:
        await update.message.reply_text("Неизвестный триггер или модель")
        return
    chat_id = update.effective_chat.id
    await asyncio.to_thread(db.clear_context, chat_id, model)
    await update.message.reply_text(f"Контекст очищен для {model}")

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправьте сообщение, начиная с триггера, например: gpt В чем смысл жизни?\n"
        "Используйте: /clear <триггер>, чтобы сбросить контекст"
    )