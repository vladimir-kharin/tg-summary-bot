"""Хранилище бота: SQLite, создание схемы и запись данных.

Схема создаётся при первом обращении. Изменения схемы по ходу разработки
делаются отдельными скриптами из папки sql/ и применяются вручную.
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id     INTEGER PRIMARY KEY,
    title       TEXT,
    first_seen  INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    date        INTEGER NOT NULL,
    user_id     INTEGER,
    user_name   TEXT,
    text        TEXT,
    reply_to    INTEGER,
    PRIMARY KEY (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_date ON messages (chat_id, date);

CREATE TABLE IF NOT EXISTS reactions (
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    emoji       TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id, user_id, emoji)
);

CREATE TABLE IF NOT EXISTS summary_state (
    chat_id         INTEGER PRIMARY KEY,
    last_message_id INTEGER,
    day             TEXT,
    day_state       TEXT,
    last_error      TEXT
);

CREATE TABLE IF NOT EXISTS summaries (
    chat_id         INTEGER NOT NULL,
    created_at      INTEGER NOT NULL,
    from_message_id INTEGER,
    to_message_id   INTEGER,
    model           TEXT,
    prompt          TEXT,
    text            TEXT,
    cost            REAL
);
"""


def connect(path):
    """Открыть базу и создать недостающие таблицы."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def save_chat(conn, chat_id, title, seen_at):
    """Запомнить чат. Название обновляется, дата первого показа — нет."""
    conn.execute(
        "INSERT INTO chats (chat_id, title, first_seen) VALUES (?, ?, ?) "
        "ON CONFLICT (chat_id) DO UPDATE SET title = excluded.title",
        (chat_id, title, seen_at),
    )


def save_messages(conn, chat_id, messages):
    """Записать сообщения. Возвращает (сколько новых, сколько уже было).

    Существующие перезаписываются: так подхватываются правки текста.
    """
    ids = [m["message_id"] for m in messages]
    known = _known_ids(conn, chat_id, ids)
    added = len(ids) - len(known)

    conn.executemany(
        "INSERT OR REPLACE INTO messages "
        "(chat_id, message_id, date, user_id, user_name, text, reply_to) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                chat_id,
                m["message_id"],
                m["date"],
                m.get("user_id"),
                m.get("user_name"),
                m.get("text"),
                m.get("reply_to"),
            )
            for m in messages
        ],
    )
    return added, len(known)


def save_reactions(conn, chat_id, message_ids, reactions):
    """Переписать реакции для указанных сообщений.

    Выгрузка и Telegram отдают текущее состояние реакций целиком,
    поэтому старые записи по этим сообщениям удаляются.
    """
    for chunk in _chunks(message_ids):
        placeholders = ",".join("?" * len(chunk))
        conn.execute(
            f"DELETE FROM reactions WHERE chat_id = ? AND message_id IN ({placeholders})",
            [chat_id, *chunk],
        )
    conn.executemany(
        "INSERT OR IGNORE INTO reactions (chat_id, message_id, user_id, emoji) "
        "VALUES (?, ?, ?, ?)",
        [(chat_id, r["message_id"], r["user_id"], r["emoji"]) for r in reactions],
    )


def set_user_reactions(conn, chat_id, message_id, user_id, emojis):
    """Заменить набор реакций одного участника на одном сообщении.

    Telegram присылает в событии текущий набор реакций этого участника
    целиком, поэтому его прежние записи удаляются. Пустой набор означает,
    что участник снял все свои реакции.
    """
    conn.execute(
        "DELETE FROM reactions WHERE chat_id = ? AND message_id = ? AND user_id = ?",
        (chat_id, message_id, user_id),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO reactions (chat_id, message_id, user_id, emoji) "
        "VALUES (?, ?, ?, ?)",
        [(chat_id, message_id, user_id, emoji) for emoji in emojis],
    )


def known_chats(conn):
    """Все чаты, которые бот когда-либо видел."""
    return conn.execute(
        "SELECT chat_id, title FROM chats ORDER BY title"
    ).fetchall()


def get_state(conn, chat_id):
    """Состояние суммаризации чата. Нет строки — считаем, что ничего не делалось."""
    row = conn.execute(
        "SELECT * FROM summary_state WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    if row:
        return dict(row)
    return {
        "chat_id": chat_id,
        "last_message_id": None,
        "day": None,
        "day_state": "waiting",
        "last_error": None,
    }


def set_day_state(conn, chat_id, day, day_state, last_error=None):
    """Записать состояние сегодняшнего саммари, сохранив отметку места."""
    conn.execute(
        "INSERT INTO summary_state (chat_id, day, day_state, last_error) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT (chat_id) DO UPDATE SET "
        "day = excluded.day, day_state = excluded.day_state, last_error = excluded.last_error",
        (chat_id, day, day_state, last_error),
    )
    conn.commit()


def mark_done(conn, chat_id, day, last_message_id):
    """Саммари опубликовано: сдвинуть отметку места и пометить день как done."""
    conn.execute(
        "INSERT INTO summary_state (chat_id, last_message_id, day, day_state, last_error) "
        "VALUES (?, ?, ?, 'done', NULL) "
        "ON CONFLICT (chat_id) DO UPDATE SET "
        "last_message_id = excluded.last_message_id, day = excluded.day, "
        "day_state = 'done', last_error = NULL",
        (chat_id, last_message_id, day),
    )
    conn.commit()


def messages_after(conn, chat_id, last_message_id):
    """Сообщения после отметки места, хронологически."""
    rows = conn.execute(
        "SELECT * FROM messages WHERE chat_id = ? AND message_id > ? ORDER BY message_id",
        (chat_id, last_message_id or 0),
    ).fetchall()
    return list(rows)


def count_pending(conn, chat_id, last_message_id):
    """Сколько сообщений накопилось после последнего саммари."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE chat_id = ? AND message_id > ?",
        (chat_id, last_message_id or 0),
    ).fetchone()
    return row["n"]


def last_messages(conn, chat_id, count):
    """Последние сообщения чата в хронологическом порядке."""
    rows = conn.execute(
        "SELECT * FROM messages WHERE chat_id = ? ORDER BY message_id DESC LIMIT ?",
        (chat_id, count),
    ).fetchall()
    return list(reversed(rows))


def reactions_for(conn, chat_id, message_ids):
    """Реакции указанных сообщений.

    Возвращает {message_id: [(эмодзи, число поставивших), ...]},
    по убыванию количества. Считается по именным записям.
    """
    result = {}
    for chunk in _chunks(message_ids):
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT message_id, emoji, COUNT(*) AS n FROM reactions "
            f"WHERE chat_id = ? AND message_id IN ({placeholders}) "
            f"GROUP BY message_id, emoji ORDER BY n DESC",
            [chat_id, *chunk],
        )
        for row in rows:
            result.setdefault(row["message_id"], []).append((row["emoji"], row["n"]))
    return result


def marked_ids(conn, chat_id, emoji, user_ids):
    """Сообщения, отмеченные заданной реакцией кем-то из указанных людей."""
    if not emoji or not user_ids:
        return set()
    placeholders = ",".join("?" * len(user_ids))
    rows = conn.execute(
        f"SELECT DISTINCT message_id FROM reactions "
        f"WHERE chat_id = ? AND emoji = ? AND user_id IN ({placeholders})",
        [chat_id, emoji, *user_ids],
    )
    return {row["message_id"] for row in rows}


def last_summary(conn, chat_id):
    """Последнее опубликованное саммари по чату."""
    return conn.execute(
        "SELECT created_at, to_message_id FROM summaries "
        "WHERE chat_id = ? ORDER BY created_at DESC LIMIT 1",
        (chat_id,),
    ).fetchone()


def _known_ids(conn, chat_id, ids):
    """Какие из переданных сообщений уже есть в базе."""
    known = set()
    for chunk in _chunks(ids):
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT message_id FROM messages WHERE chat_id = ? "
            f"AND message_id IN ({placeholders})",
            [chat_id, *chunk],
        )
        known.update(row["message_id"] for row in rows)
    return known


def _chunks(items, size=500):
    """SQLite ограничивает число параметров в запросе, поэтому режем на части."""
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]
