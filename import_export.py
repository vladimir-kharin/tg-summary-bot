"""Загрузка выгрузки Telegram Desktop в базу бота.

Запуск:
    python import_export.py <путь к result.json> [путь к базе]
"""

import sys
import time

import db
import tg_export


def import_file(conn, export_path):
    """Разобрать выгрузку и записать в уже открытую базу. Возвращает отчёт."""
    data = tg_export.parse(export_path)

    db.save_chat(conn, data["chat_id"], data["title"], int(time.time()))
    added, existed = db.save_messages(conn, data["chat_id"], data["messages"])
    db.save_reactions(
        conn,
        data["chat_id"],
        [m["message_id"] for m in data["messages"]],
        data["reactions"],
    )
    conn.commit()

    return {
        "chat_id": data["chat_id"],
        "title": data["title"],
        "added": added,
        "existed": existed,
        "skipped": data["skipped"],
        "reactions": len(data["reactions"]),
        "period": period(data["messages"]),
    }


def run(export_path, db_path):
    """Импорт из командной строки: открыть базу и залить выгрузку."""
    return import_file(db.connect(db_path), export_path)


def period(messages):
    """Даты первого и последнего сообщения выгрузки."""
    if not messages:
        return None
    dates = [m["date"] for m in messages]
    fmt = "%d.%m.%Y %H:%M"
    return (
        time.strftime(fmt, time.localtime(min(dates))),
        time.strftime(fmt, time.localtime(max(dates))),
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    result = run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "data/bot.db")

    print(f"Чат: {result['title']} (id {result['chat_id']})")
    if result["period"]:
        print(f"Период: {result['period'][0]} — {result['period'][1]}")
    print(f"Добавлено новых: {result['added']}")
    print(f"Уже было: {result['existed']}")
    print(f"Пропущено без текста: {result['skipped']}")
    print(f"Реакций: {result['reactions']}")
