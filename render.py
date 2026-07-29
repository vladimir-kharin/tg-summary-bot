"""Подготовка данных для промпта: сборка <msg>-блоков и подстановка в шаблон.

Формат блоков и правила — в требования_к_данным.md.
"""

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def render_prompt(template, rows, reactions_map, marked_ids):
    """Подставить сообщения в шаблон промпта.

    template     — текст промпта с плейсхолдерами {{MESSAGES}} и {{HIGHLIGHTED}}
    rows         — сообщения периода, хронологически
    reactions_map — {message_id: [(эмодзи, число), ...]}
    marked_ids   — множество id отмеченных сообщений (для {{HIGHLIGHTED}})
    """
    names = latest_names(rows)

    all_blocks = "\n".join(msg_block(r, names, reactions_map) for r in rows)
    highlighted = "\n".join(
        msg_block(r, names, reactions_map) for r in rows if r["message_id"] in marked_ids
    )

    return template.replace("{{MESSAGES}}", all_blocks).replace("{{HIGHLIGHTED}}", highlighted)


def latest_names(rows):
    """Для каждого автора — самое свежее его имя за период.

    Участник, сменивший имя в профиле, попадает в базу под несколькими;
    сводим к одному, иначе промпт посчитает его за двоих.
    """
    names = {}
    for row in rows:  # rows идут от старых к новым, поэтому позднее имя перезапишет раннее
        if row["user_id"] is not None and row["user_name"]:
            names[row["user_id"]] = row["user_name"]
    return names


def msg_block(row, names, reactions_map):
    """Один <msg>-блок по сообщению."""
    attrs = [f'id="{row["message_id"]}"', f'time="{msk_time(row["date"])}"']

    name = names.get(row["user_id"]) or (row["user_name"] if row["user_id"] is None else None)
    if name:
        attrs.append(f'from="{name}"')

    if row["reply_to"]:
        attrs.append(f'reply="{row["reply_to"]}"')

    reactions = reactions_map.get(row["message_id"])
    if reactions:
        pairs = " ".join(f"{emoji}{count}" for emoji, count in reactions)
        attrs.append(f'reactions="{pairs}"')

    text = (row["text"] or "").replace("</msg>", "< /msg>")
    return f"<msg {' '.join(attrs)}>\n{text}\n</msg>"


def msk_time(unixtime):
    """Метка времени в московское время, формат ГГГГ-ММ-ДД ЧЧ:ММ."""
    return datetime.fromtimestamp(unixtime, MSK).strftime("%Y-%m-%d %H:%M")
