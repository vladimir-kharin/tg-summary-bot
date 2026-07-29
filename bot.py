"""Бот суммаризации чатов.

Принимает сообщения по long polling и складывает их в ту же базу,
в которую пишет импорт выгрузок. Публикацией сводок по расписанию
занимается scheduler, командами владельца в личке — обработчики ниже.
"""

import asyncio
import logging
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import config
import db
import import_export
import scheduler
import summarizer

DEFAULT_TEST_COUNT = 30
# Облачный Bot API отдаёт файлы не больше 20 МБ
MAX_IMPORT_BYTES = 20 * 1024 * 1024

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "bot.db"
LOG_PATH = DATA_DIR / "bot.log"

log = logging.getLogger("bot")
dp = Dispatcher()
conn = None


def setup_logging():
    """Лог событий в файл с ротацией: три копии по 10 МБ."""
    DATA_DIR.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler())


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def on_chat_message(message: Message):
    """Сохранить сообщение группы. Нетекстовые пропускаем."""
    text = message.text or message.caption
    if not text:
        return

    chat = message.chat
    db.save_chat(conn, chat.id, chat.title, int(time.time()))
    db.save_messages(conn, chat.id, [{
        "message_id": message.message_id,
        "date": int(message.date.timestamp()),
        "user_id": message.from_user.id if message.from_user else None,
        "user_name": message.from_user.full_name if message.from_user else None,
        "text": text,
        "reply_to": message.reply_to_message.message_id if message.reply_to_message else None,
    }])
    conn.commit()


@dp.message(F.chat.type == "private", F.document)
async def on_import(message: Message):
    """Приём выгрузки Telegram Desktop: владелец шлёт боту файл result.json."""
    if message.from_user.id != config.env()["owner_id"]:
        return

    document = message.document
    if not document.file_name.lower().endswith(".json"):
        await message.answer("Жду файл выгрузки Telegram Desktop — result.json.")
        return
    if document.file_size and document.file_size > MAX_IMPORT_BYTES:
        await message.answer(
            "Файл больше 20 МБ — столько облачный Telegram боту не отдаёт. "
            "Выгрузите чат по частям (за меньший период) и пришлите частями."
        )
        return

    await message.answer("Загружаю выгрузку…")
    tmp_path = DATA_DIR / f"import_{document.file_unique_id}.json"
    try:
        await message.bot.download(document, destination=tmp_path)
        report = import_export.import_file(conn, tmp_path)
    except Exception as error:
        log.exception("Импорт выгрузки не удался")
        await message.answer(f"Не получилось разобрать файл: {type(error).__name__} {error}")
        return
    finally:
        tmp_path.unlink(missing_ok=True)

    log.info("Импорт: чат %s, добавлено %s, было %s",
             report["chat_id"], report["added"], report["existed"])
    await message.answer(import_report(report))


def import_report(report):
    """Текст отчёта об импорте выгрузки."""
    lines = [f"Чат: {report['title']} (id {report['chat_id']})"]
    if report["period"]:
        lines.append(f"Период: {report['period'][0]} — {report['period'][1]}")
    lines.append(f"Добавлено новых: {report['added']}")
    lines.append(f"Уже было: {report['existed']}")
    lines.append(f"Пропущено без текста: {report['skipped']}")
    lines.append(f"Реакций: {report['reactions']}")

    _, configured = config.chats()
    if report["chat_id"] not in configured:
        lines.append("")
        lines.append("Чат не в настройках — сообщения сохранены, саммари по нему не делается.")
    return "\n".join(lines)


@dp.message(Command("status"), F.chat.type == "private")
async def on_status(message: Message):
    """Состояние чатов: накопление, состояние дня, ошибки."""
    if message.from_user.id != config.env()["owner_id"]:
        return
    await message.answer(build_status())


@dp.message(Command("test"), F.chat.type == "private")
async def on_test(message: Message, command: CommandObject):
    """Тестовое саммари: /test [сколько сообщений] [id чата].

    Отметку «досюда просуммировано» не трогает.
    """
    if message.from_user.id != config.env()["owner_id"]:
        return

    try:
        count, chat_id = parse_test_args(command.args)
    except ValueError as error:
        await message.answer(str(error))
        return

    rows = db.last_messages(conn, chat_id, count)
    if not rows:
        await message.answer("По этому чату в базе нет сообщений.")
        return

    _, configured = config.chats()
    settings = configured.get(chat_id, {})
    prompt_name = settings.get("prompt") or "default"

    await message.answer(f"Делаю саммари по {plural(len(rows), 'сообщению', 'сообщениям', 'сообщениям')}…")
    try:
        result = await summarizer.summarize(
            conn, config.env(), chat_id, rows, prompt_name, settings.get("mark")
        )
    except Exception as error:
        log.exception("Тестовое саммари не удалось")
        await message.answer(f"Не получилось: {type(error).__name__} {error}")
        return

    log.info("Тест: чат %s, %s сообщений, промпт %s", chat_id, len(rows), prompt_name)
    await message.answer(result["html"], parse_mode="HTML", disable_web_page_preview=True)
    await message.answer(service_line(result, prompt_name))


def parse_test_args(args):
    """Разобрать параметры команды /test."""
    parts = (args or "").split()
    count = DEFAULT_TEST_COUNT
    chat_id = None

    if parts:
        if not parts[0].lstrip("-").isdigit():
            raise ValueError("Первым параметром идёт количество сообщений, числом.")
        count = int(parts[0])
    if len(parts) > 1:
        if not parts[1].lstrip("-").isdigit():
            raise ValueError("Вторым параметром идёт id чата, числом.")
        chat_id = int(parts[1])

    if chat_id is None:
        default_chat, _ = config.chats()
        if not default_chat:
            raise ValueError("Чат по умолчанию не задан, укажите id чата.")
        chat_id = int(default_chat)

    if count < 1:
        raise ValueError("Количество сообщений должно быть больше нуля.")
    return count, chat_id


def service_line(result, prompt_name):
    """Служебная строка теста: параметры прогона. Отдельным сообщением, без разметки."""
    cost = f"${result['cost']:.4f}" if result.get("cost") is not None else "стоимость неизвестна"
    parts = [
        plural(result["count"], "сообщение", "сообщения", "сообщений"),
        result["model"],
        f"промпт {prompt_name}",
        f"{result['seconds']} с",
        cost,
    ]
    line = "— " + " | ".join(parts)
    if result["truncated"]:
        line += "\n⚠ саммари обрезано по длине"
    return line


def build_status():
    """Собрать текст ответа команды /status."""
    default_chat, configured = config.chats()
    known = {row["chat_id"]: row["title"] for row in db.known_chats(conn)}

    lines = ["Чаты с настройками"]
    if not configured:
        lines.append("• нет ни одного")

    for chat_id, settings in configured.items():
        state = db.get_state(conn, chat_id)
        pending = db.count_pending(conn, chat_id, state["last_message_id"])
        title = settings["name"] or known.get(chat_id) or "без названия"

        lines.append(f"• {title} — id {chat_id}")
        lines.append(f"  накопилось {plural(pending, 'сообщение', 'сообщения', 'сообщений')}"
                     f" (порог {settings['min_messages']})")
        lines.append(f"  {describe_last_summary(chat_id)}")
        lines.append(f"  сегодня: {describe_day(state, settings['time'])}")

    extra = [cid for cid in known if cid not in configured]
    if extra:
        lines.append("")
        lines.append("Без настроек")
        for chat_id in extra:
            total = db.count_pending(conn, chat_id, 0)
            lines.append(f"• {known[chat_id]} — id {chat_id}")
            lines.append(f"  {plural(total, 'сообщение', 'сообщения', 'сообщений')}"
                         f" в базе, саммари не делается")

    lines.append("")
    default_title = configured.get(default_chat, {}).get("name") if default_chat else None
    lines.append(f"Чат по умолчанию: {default_title or default_chat or 'не задан'}")
    return "\n".join(lines)


def plural(count, one, few, many):
    """Согласовать существительное с числом: 1 сообщение, 2 сообщения, 5 сообщений."""
    tail_two = count % 100
    tail = count % 10
    if 11 <= tail_two <= 14:
        word = many
    elif tail == 1:
        word = one
    elif 2 <= tail <= 4:
        word = few
    else:
        word = many
    return f"{count} {word}"


def describe_last_summary(chat_id):
    row = db.last_summary(conn, chat_id)
    if not row:
        return "саммари ещё не делалось"
    when = datetime.fromtimestamp(row["created_at"]).strftime("%d.%m %H:%M")
    return f"последнее саммари {when}, по сообщение {row['to_message_id']}"


def describe_day(state, run_time):
    """Состояние сегодняшнего саммари плюс время ближайшего запуска."""
    if not run_time:
        return "автопубликация выключена (время не задано)"

    today = datetime.now().strftime("%Y-%m-%d")
    day_state = state["day_state"] if state["day"] == today else "waiting"

    if day_state == "failed" and state["last_error"]:
        return f"failed — {state['last_error']}"
    if day_state in ("done", "skipped"):
        return f"{day_state}, следующий запуск завтра в {run_time}"
    return f"waiting, запуск в {run_time}"


async def main():
    global conn
    setup_logging()
    DATA_DIR.mkdir(exist_ok=True)
    conn = db.connect(DB_PATH)

    settings = config.env()
    session = AiohttpSession(proxy=settings["proxy"]) if settings["proxy"] else None
    bot = Bot(token=settings["token"], session=session)

    me = await bot.get_me()
    log.info("Бот запущен: @%s", me.username)
    asyncio.create_task(scheduler.run(bot, conn))
    await dp.start_polling(bot, allowed_updates=[
        "message", "edited_message", "channel_post", "edited_channel_post",
        "my_chat_member", "message_reaction",
    ])


if __name__ == "__main__":
    asyncio.run(main())
