"""Превращение ответа модели в готовый HTML-пост.

Модель отдаёт простой текст с двумя метками: `## ` в начале строки — заголовок,
`[[id]]` — место ссылки на обсуждение. Всю разметку накладывает бот.
Порядок и правила — в требования_к_данным.md, раздел про обработку ответа.
"""

import logging
import re

log = logging.getLogger("bot")

MAX_RAW = 4000
LINK_TEXT = "→ обсуждение"
HASHTAG = "#сводка"

HEADER_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
MARKER_RE = re.compile(r"\[\[(\d+)\]\]")


def to_post(raw_text, valid_ids, chat_id):
    """Собрать HTML-пост из ответа модели.

    raw_text  — текст модели как есть
    valid_ids — множество message_id, переданных модели в этом прогоне
    chat_id   — для сборки ссылки на обсуждение

    Возвращает (html, truncated), где truncated — была ли обрезка по длине.
    """
    text, truncated = clip(raw_text)
    text = escape(text)
    text = HEADER_RE.sub(r"<b>\1</b>", text)
    text = link_markers(text, valid_ids, chat_id)
    text = f"{text}\n\n{HASHTAG}"
    return text, truncated


def clip(text):
    """Шаг 1: обрезать сырой текст, если длиннее предела."""
    if len(text) > MAX_RAW:
        log.info("Саммари обрезано по длине: %s знаков", len(text))
        return text[:MAX_RAW], True
    return text, False


def escape(text):
    """Шаг 2: экранировать пользовательский текст. Амперсанд первым."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def link_markers(text, valid_ids, chat_id):
    """Шаг 4: метки `[[id]]` — в ссылки; метки с чужими номерами удалить."""
    prefix = link_prefix(chat_id)

    def replace(match):
        message_id = int(match.group(1))
        if message_id in valid_ids:
            return f'<a href="{prefix}/{message_id}">{LINK_TEXT}</a>'
        log.info("Метка с посторонним номером %s удалена", message_id)
        return ""

    return MARKER_RE.sub(replace, text)


def link_prefix(chat_id):
    """Префикс ссылки на сообщение приватной супергруппы: t.me/c/<id без -100>."""
    return f"https://t.me/c/{str(chat_id).removeprefix('-100')}"
