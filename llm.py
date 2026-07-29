"""Обращение к модели через OpenRouter.

Стоимость запроса OpenRouter возвращает сам, в поле usage.cost —
включать её отдельно не нужно.
"""

import time
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).parent
PROMPTS_DIR = BASE_DIR / "prompts"
API_URL = "https://openrouter.ai/api/v1/chat/completions"


def load_prompt(name):
    """Прочитать промпт из файла. Читается при каждом обращении,
    поэтому правка файла действует без перезапуска бота."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Нет файла промпта: {path}")
    return path.read_text(encoding="utf-8")


async def complete(settings, prompt_text):
    """Отправить модели готовый промпт и вернуть ответ.

    Промпт самодостаточен — роль, инструкции и данные уже внутри,
    поэтому отдаётся одним сообщением. Возвращает текст, стоимость,
    имя модели и число секунд.
    """
    started = time.monotonic()
    response = await ask(settings, prompt_text)
    response["seconds"] = round(time.monotonic() - started, 1)
    return response


async def ask(settings, prompt_text):
    """Один запрос к модели."""
    headers = {
        "Authorization": f"Bearer {settings['openrouter_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings["model"],
        "messages": [
            {"role": "user", "content": prompt_text},
        ],
    }

    client_args = {"timeout": 300}
    if settings["proxy"]:
        client_args["proxy"] = settings["proxy"]

    async with httpx.AsyncClient(**client_args) as client:
        result = await client.post(API_URL, headers=headers, json=payload)
        result.raise_for_status()
        data = result.json()

    usage = data.get("usage") or {}
    return {
        "text": data["choices"][0]["message"]["content"].strip(),
        "cost": usage.get("cost"),
        "model": data.get("model") or settings["model"],
    }
