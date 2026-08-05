# Базовый образ с Python 3.11
FROM python:3.11-slim

# Установить переменные окружения
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Установить системные зависимости для PostgreSQL и других инструментов
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Установить рабочую директорию
WORKDIR /app

# Копировать файл зависимостей и установить их
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Копировать весь проект
COPY . .

# Создать директорию для статики
RUN mkdir -p /app/staticfiles

# Собрать статические файлы
RUN python manage.py collectstatic --noinput 2>/dev/null || true

# Открыть порт
EXPOSE 8000

# Команда запуска
CMD ["gunicorn", "warehouse_config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]
