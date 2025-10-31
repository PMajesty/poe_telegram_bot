import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
POE_API_KEY = os.getenv("POE_API_KEY")
POE_BASE_URL = os.getenv("POE_BASE_URL", "https://api.poe.com/v1")

DB_CONFIG = {
    "NAME": os.getenv("DB_NAME"),
    "USER": os.getenv("DB_USER"),
    "PASSWORD": os.getenv("DB_PASSWORD"),
    "HOST": os.getenv("DB_HOST", "localhost"),
    "PORT": os.getenv("DB_PORT", "5432"),
}

CONTEXT_MAX_MESSAGES = 5

TEXT_BOT_CONFIGS = {
    ("gpt",): "GPT-5",
    ("o3",): "o3",
    ("gem", "гем"): "Gemini-2.5-Pro",
    ("jam", "джем"): "EVILMENI",
    ("кратко", "кракто"): "GEMSHORT",
    ("джонни",): "JOHHNNYSILVERHAND",
    ("пахом",): "creativebottt",
    ("flash", "флеш"): "Gemini-2.5-Flash",
    ("злод",): "EVILAUDE",
    ("клод",): "Claude-3.5-Sonnet",
}

IMAGE_BOT_CONFIGS = {
    ("flashimage",): "Gemini-2.0-Flash-Exp",
    ("flashimageturbo", "banana", "nano", "нано"): "Gemini-2.5-Flash-Image",
}

BOT_CONFIGS = {**TEXT_BOT_CONFIGS, **IMAGE_BOT_CONFIGS}

IMAGE_BOT_MODELS = set(IMAGE_BOT_CONFIGS.values())

WEB_SEARCH_BOTS = {
    "GPT-5",
    "Gemini-2.5-Pro",
    "EVILMENI",
    "GEMSHORT",
    "Gemini-2.5-Flash",
}

ECONOMY_BOT_MODELS = {"Gemini-2.5-Flash"}
ECONOMY_BOTS = {"Gemini-2.5-Flash"}

ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
UPLOAD_PROXY_URL = os.getenv("UPLOAD_PROXY_URL")