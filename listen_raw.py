"""Отладка: слушать getUpdates напрямую и печатать всё, что приходит.

Запуск: python listen_raw.py [сколько секунд слушать]
Нужен, когда непонятно, доходят ли обновления от Telegram вообще.
"""

import asyncio
import json
import sys
import time

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

import config


async def main(seconds):
    settings = config.env()
    bot = Bot(
        token=settings["token"],
        session=AiohttpSession(proxy=settings["proxy"]) if settings["proxy"] else None,
    )

    print(f"Слушаю {seconds} секунд. Пишите сообщения в группу.", flush=True)
    deadline = time.time() + seconds
    offset = None
    count = 0

    while time.time() < deadline:
        try:
            updates = await bot.get_updates(offset=offset, timeout=20, allowed_updates=[])
        except Exception as error:
            print("ОШИБКА запроса:", type(error).__name__, error, flush=True)
            await asyncio.sleep(3)
            continue

        for update in updates:
            count += 1
            offset = update.update_id + 1
            print("---", flush=True)
            print(json.dumps(update.model_dump(exclude_none=True), ensure_ascii=False,
                             default=str, indent=1), flush=True)

    print(f"Итого получено обновлений: {count}", flush=True)
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 60))
