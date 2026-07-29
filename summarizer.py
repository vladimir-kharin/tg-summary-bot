"""Сборка саммари: данные из базы → промпт → модель → готовый HTML-пост.

Общий путь для тестового прогона и рабочей публикации.
"""

import db
import llm
import postprocess
import render


async def summarize(conn, settings, chat_id, rows, prompt_name, mark):
    """Сделать саммари по переданным сообщениям.

    rows   — сообщения, хронологически
    mark    — настройка метки чата (reaction, by) или None

    Возвращает словарь: html, raw, model, cost, seconds, truncated, count.
    """
    ids = [r["message_id"] for r in rows]
    reactions_map = db.reactions_for(conn, chat_id, ids)

    marked = set()
    if mark and mark.get("reaction") and mark.get("by"):
        marked = db.marked_ids(conn, chat_id, mark["reaction"], mark["by"]) & set(ids)

    template = llm.load_prompt(prompt_name)
    prompt_text = render.render_prompt(template, rows, reactions_map, marked)

    response = await llm.complete(settings, prompt_text)
    html, truncated = postprocess.to_post(response["text"], set(ids), chat_id)

    return {
        "html": html,
        "raw": response["text"],
        "model": response["model"],
        "cost": response["cost"],
        "seconds": response["seconds"],
        "truncated": truncated,
        "count": len(rows),
    }
