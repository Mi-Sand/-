# Образ системы складского учёта ООО «ЛЕКО».
#
# Python 3.12: на 3.11 установилась бы Django 5.2 вместо 6.0 — работает и
# так, но лучше держать образ на той же версии, что проверена в тестах.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Библиотеки для PostgreSQL. gcc нужен на случай, если для какой-то
# зависимости не найдётся готового пакета и её придётся собирать.
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости ставятся до копирования кода: пока requirements.txt не
# менялся, этот слой берётся из кэша, и пересборка после правки кода
# занимает секунды вместо минут.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Сборка статики на этапе образа, а не при запуске: так ошибка вылезет
# при сборке, когда её видно, а не при старте контейнера в проде.
#
# Ключ важен: раньше здесь стояло `|| true`, и падение collectstatic
# проглатывалось. Образ собирался «успешно» и разваливался позже —
# уже без внятного объяснения.
#
# SECRET_KEY на время сборки задаётся временный: настоящий приходит
# переменной окружения при запуске, а collectstatic до базы не ходит и
# ключом не пользуется.
RUN SECRET_KEY=build-time-only DEBUG=False \
    python manage.py collectstatic --noinput

# Каталоги для данных, которые монтируются томами
RUN mkdir -p /app/media /app/logs

EXPOSE 8000

# Проверка живости: контейнер считается здоровым, когда отвечает страница
# входа. Без неё docker compose считал бы контейнер рабочим сразу после
# запуска процесса, даже если приложение упало при инициализации.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; \
urllib.request.urlopen('http://127.0.0.1:8000/login/').read()" || exit 1

# Потоки в дополнение к процессам: запросы в этой системе почти всё время
# ждут базу, а не считают, и потоки дешевле процессов.
CMD ["gunicorn", "warehouse_config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--threads", "2", \
     "--access-logfile", "-", "--error-logfile", "-"]
