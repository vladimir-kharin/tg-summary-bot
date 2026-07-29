"""Разбор выгрузки чата из Telegram Desktop (result.json).

Приводит выгрузку к тем же полям, в которых бот сохраняет живой поток,
чтобы оба источника писались в одну базу.
"""

import json


def parse(path):
    """Разобрать файл выгрузки.

    Возвращает словарь: chat_id, title, messages, reactions, skipped
    (skipped — сколько сообщений пропущено как нетекстовые).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    chat_id = to_bot_api_chat_id(data["id"], data.get("type", ""))
    messages, reactions, skipped = [], [], 0

    for item in data.get("messages", []):
        if item.get("type") != "message":
            continue  # служебные записи: вход в чат, закрепы, смена названия

        text = extract_text(item)
        if not text:
            skipped += 1  # вложение без подписи — сохранять нечего
            continue

        message_id = item["id"]
        messages.append({
            "message_id": message_id,
            "date": int(item["date_unixtime"]),
            "user_id": extract_user_id(item.get("from_id")),
            "user_name": item.get("from"),
            "text": text,
            "reply_to": item.get("reply_to_message_id"),
        })
        reactions.extend(extract_reactions(item, message_id))

    return {
        "chat_id": chat_id,
        "title": data.get("name"),
        "messages": messages,
        "reactions": reactions,
        "skipped": skipped,
    }


def to_bot_api_chat_id(raw_id, chat_type):
    """Идентификатор чата из выгрузки — в вид, который использует Bot API.

    В выгрузке номер супергруппы или канала записан без знака и без префикса,
    а Bot API тот же чат называет -100<номер>. Без приведения живой поток
    и импорт легли бы в базу как два разных чата.
    """
    if "supergroup" in chat_type or "channel" in chat_type:
        return int(f"-100{raw_id}")
    if "group" in chat_type:
        return -int(raw_id)
    return int(raw_id)


def extract_text(item):
    """Собрать плоский текст сообщения.

    Поле text бывает строкой, а при ссылках и форматировании — списком кусков.
    В text_entities текст всегда разложен по частям, поэтому берём его.
    """
    entities = item.get("text_entities")
    if entities:
        return "".join(part.get("text", "") for part in entities).strip()

    text = item.get("text")
    if isinstance(text, str):
        return text.strip()
    if isinstance(text, list):
        return "".join(
            part if isinstance(part, str) else part.get("text", "") for part in text
        ).strip()
    return ""


def extract_user_id(from_id):
    """Числовой идентификатор автора из значения вида "user123456789".

    Сообщения от имени канала или анонимного администратора выглядят иначе —
    у них автора нет, остаётся только отображаемое имя.
    """
    if isinstance(from_id, str) and from_id.startswith("user"):
        return int(from_id[4:])
    return None


def extract_reactions(item, message_id):
    """Реакции сообщения — по одной записи на человека.

    Выгрузка перечисляет поставивших в поле recent. Telegram показывает там
    не всех, если реакций много, поэтому полнота не гарантирована.
    """
    result = []
    for reaction in item.get("reactions", []):
        if reaction.get("type") != "emoji":
            continue  # премиум-реакции своими картинками не поддерживаем
        emoji = reaction.get("emoji")
        for who in reaction.get("recent", []):
            user_id = extract_user_id(who.get("from_id"))
            if user_id is not None:
                result.append({
                    "message_id": message_id,
                    "user_id": user_id,
                    "emoji": emoji,
                })
    return result
