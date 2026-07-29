FROM python:3.12-slim

# Лог и планировщик — по Москве
ENV TZ=Europe/Moscow
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Сначала зависимости — чтобы слой кэшировался, пока requirements не менялся
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код бота. Данные, настройки и промпты подключаются томами, в образ не кладутся
COPY *.py ./

CMD ["python", "bot.py"]
