"""Настройки: секреты из .env и список чатов из config/chats.yaml.

Файл чатов перечитывается при каждом обращении — правка настроек
действует без перезапуска бота.
"""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
CHATS_FILE = BASE_DIR / "config" / "chats.yaml"

# Значения, которые подставляются, если в настройках чата их нет.
# time по умолчанию нет: без него автопубликация выключена, чат только копится.
DEFAULTS = {
    "name": None,
    "prompt": "default",
    "min_messages": 30,
    "time": None,
    "mark": None,
}


def env():
    """Секреты и параметры подключения."""
    load_dotenv(BASE_DIR / ".env", override=True)
    owner = os.getenv("OWNER_ID", "").strip()
    return {
        "token": os.getenv("TELEGRAM_TOKEN", "").strip(),
        "owner_id": int(owner) if owner else None,
        "openrouter_key": os.getenv("OPENROUTER_KEY", "").strip(),
        "model": os.getenv("MODEL", "").strip(),
        "proxy": os.getenv("PROXY", "").strip() or None,
    }


def chats():
    """Настройки чатов.

    Возвращает (id чата по умолчанию, {id чата: настройки}).
    Присутствие чата в файле означает «суммаризировать»; всё остальное,
    что бот видит, просто копится в базе.
    """
    if not CHATS_FILE.exists():
        return None, {}

    data = yaml.safe_load(CHATS_FILE.read_text(encoding="utf-8")) or {}
    result = {}
    for chat_id, settings in (data.get("chats") or {}).items():
        merged = dict(DEFAULTS)
        merged.update(settings or {})
        result[int(chat_id)] = merged
    return data.get("default_chat"), result
