"""Планировщик автопубликации.

Раз в TICK_SECONDS проверяет каждый настроенный чат и, если пришло время,
делает и публикует саммари. Логика одного состояния на день — в спецификации.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import config
import db
import summarizer

log = logging.getLogger("bot")

MSK = timezone(timedelta(hours=3))
TICK_SECONDS = 300


async def run(bot, conn):
    """Вечный цикл проверки расписания."""
    while True:
        try:
            await tick(bot, conn)
        except Exception:
            log.exception("Сбой в цикле планировщика")
        await asyncio.sleep(TICK_SECONDS)


async def tick(bot, conn):
    """Один проход по всем настроенным чатам."""
    settings = config.env()
    _, configured = config.chats()
    now = datetime.now(MSK)
    today = now.strftime("%Y-%m-%d")

    for chat_id, chat in configured.items():
        if not chat.get("time"):
            continue  # автопубликация выключена — чат только копится
        await handle_chat(bot, conn, settings, chat_id, chat, now, today)


async def handle_chat(bot, conn, settings, chat_id, chat, now, today):
    """Решить и, если надо, сделать саммари по одному чату.

    waiting → набралось: делаем; не набралось: skipped.
    failed  → повторяем попытку.
    done/skipped → до завтра ничего.
    """
    if now.strftime("%H:%M") < chat["time"]:
        return  # назначенное время ещё не наступило

    state = db.get_state(conn, chat_id)
    day_state = state["day_state"] if state["day"] == today else "waiting"
    if day_state in ("done", "skipped"):
        return

    rows = db.messages_after(conn, chat_id, state["last_message_id"])
    if day_state == "waiting" and len(rows) < chat["min_messages"]:
        db.set_day_state(conn, chat_id, today, "skipped")
        log.info("Чат %s: %s сообщений (< %s), день пропущен",
                 chat_id, len(rows), chat["min_messages"])
        return

    if not rows:  # ветка failed без новых сообщений — публиковать нечего
        db.set_day_state(conn, chat_id, today, "skipped")
        return

    await publish(bot, conn, settings, chat_id, chat, today, rows)


async def publish(bot, conn, settings, chat_id, chat, today, rows):
    """Сделать саммари, опубликовать в чат, сдвинуть отметку места."""
    prompt_name = chat.get("prompt") or "default"
    try:
        result = await summarizer.summarize(
            conn, settings, chat_id, rows, prompt_name, chat.get("mark")
        )
        await bot.send_message(
            chat_id, result["html"], parse_mode="HTML", disable_web_page_preview=True
        )
    except Exception as error:
        db.set_day_state(conn, chat_id, today, "failed", f"{type(error).__name__}: {error}")
        log.exception("Чат %s: саммари не опубликовано", chat_id)
        return

    last_id = rows[-1]["message_id"]
    db.mark_done(conn, chat_id, today, last_id)
    save_summary(conn, chat_id, result, prompt_name, rows)
    log.info("Чат %s: саммари опубликовано, %s сообщений, по %s",
             chat_id, len(rows), last_id)


def save_summary(conn, chat_id, result, prompt_name, rows):
    """Записать опубликованное саммари в историю."""
    conn.execute(
        "INSERT INTO summaries "
        "(chat_id, created_at, from_message_id, to_message_id, model, prompt, text, cost) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            chat_id,
            int(datetime.now(MSK).timestamp()),
            rows[0]["message_id"],
            rows[-1]["message_id"],
            result["model"],
            prompt_name,
            result["raw"],
            result["cost"],
        ),
    )
    conn.commit()
