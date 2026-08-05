# Развёртывание в Docker

Способ поставить систему на сервер так, чтобы она работала постоянно:
PostgreSQL, приложение под gunicorn и nginx впереди — три контейнера,
поднимаются одной командой.

## Что нужно

- Docker 20.10+ и Docker Compose 2.0+
  ([Docker Desktop](https://www.docker.com/products/docker-desktop) для
  Windows и macOS, [Docker Engine](https://docs.docker.com/engine/install/)
  для Linux)
- 2 ГБ свободной памяти и ~2 ГБ на диске

Проверить, что всё на месте:

```bash
docker --version
docker compose version
```

---

## Запуск

### 1. Настройки

```bash
cd warehouse_project
cp .env.example .env
```

Откройте `.env` и заполните три строки:

```ini
SECRET_KEY=<результат команды ниже>
DB_PASSWORD=<придумайте пароль для базы>
ALLOWED_HOSTS=localhost,127.0.0.1,<адрес или домен сервера>
```

Ключ:

```bash
docker run --rm python:3.12-slim python -c \
  "import secrets; print(secrets.token_urlsafe(64))"
```

`DEBUG` оставьте `False` — это боевой режим.

> **Тот же `.env` читают и Django, и compose.** Пароль базы берётся отсюда
> в оба места, поэтому менять его нужно в одном файле. Если поднимали
> систему раньше с другим паролем, поменять его задним числом не выйдет —
> он уже записан в томе с базой; см. раздел про ошибки ниже.

### 2. Сборка и запуск

```bash
docker compose up -d --build
```

Первый раз занимает 3–5 минут: скачиваются образы и ставятся зависимости.
Дальше — секунды.

### 3. Администратор

```bash
docker compose exec web python manage.py createsuperuser
```

### 4. Проверка

```bash
docker compose exec web python manage.py checksetup --production
```

Пока команда не скажет «Всё в порядке», раздавать адрес сотрудникам рано.

Открыть: **<http://localhost>**

---

## Что именно поднимается

| Служба | Образ | Наружу | Зачем |
|---|---|---|---|
| `db` | postgres:16-alpine | нет | База данных |
| `web` | собирается из `Dockerfile` | нет | Приложение под gunicorn |
| `nginx` | nginx:alpine | порт 80 | Отдаёт статику и фото, остальное передаёт приложению |

Наружу выведен только nginx. Порты базы и приложения намеренно закрыты:
открытый 5432 — это прямой доступ к данным предприятия из локальной сети,
а открытый 8000 обходил бы настройки nginx.

Порядок запуска соблюдается сам: `web` ждёт, пока база ответит на
`pg_isready`, а `nginx` — пока приложение начнёт отдавать страницу входа.

### Где лежат данные

| Что | Где | Переживает пересборку |
|---|---|---|
| База данных | том `postgres_data` | да |
| Фотографии товаров | каталог `./media` | да |
| Журналы | каталог `./logs` | да |
| Собранная статика | том `staticfiles` | пересобирается |

Фотографии лежат на диске хоста, а не в томе, — чтобы их можно было
копировать обычными средствами, не заходя в Docker.

---

## Повседневные команды

```bash
docker compose ps                    # что запущено
docker compose logs -f web           # журнал приложения
docker compose logs -f               # журнал всех служб
docker compose restart web           # перезапустить приложение
docker compose down                  # остановить (данные сохраняются)
docker compose up -d                 # запустить снова
```

Выполнить команду Django:

```bash
docker compose exec web python manage.py checksetup
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py changepassword ivanov
```

Зайти внутрь:

```bash
docker compose exec web bash
docker compose exec db psql -U warehouse_user -d warehouse_db
```

---

## Обновление системы

```bash
git pull                              # или распакуйте новую версию
docker compose up -d --build          # пересобрать и перезапустить
docker compose exec web python manage.py checksetup --production
```

Миграции применяются при старте контейнера автоматически — они лежат в
репозитории, а не создаются на сервере.

---

## Резервное копирование

**База:**

```bash
docker compose exec -T db pg_dump -U warehouse_user warehouse_db \
  | gzip > backup_$(date +%F).sql.gz
```

**Фотографии товаров:**

```bash
tar czf media_$(date +%F).tar.gz media/
```

**Восстановление базы:**

```bash
gunzip -c backup_2026-08-05.sql.gz \
  | docker compose exec -T db psql -U warehouse_user -d warehouse_db
```

Поставьте копирование на расписание (`cron` в Linux, «Планировщик заданий»
в Windows) и **хотя бы раз проверьте, что копия разворачивается.** Копия,
которую никогда не пробовали восстановить, — это ещё не копия.

---

## HTTPS

Для доступа снаружи локальной сети нужен сертификат. Проще всего —
Let's Encrypt:

```bash
docker run --rm -p 80:80 -v "$PWD/certs:/etc/letsencrypt" \
  certbot/certbot certonly --standalone -d sklad.вашдомен.ру
```

Затем в `nginx.conf` добавьте второй блок `server` на 443-м порту с
`ssl_certificate` и `ssl_certificate_key`, а на 80-м оставьте
перенаправление на HTTPS. В `docker-compose.yml` пробросьте порт 443 и
подключите каталог `./certs` томом к nginx.

Не забудьте про продление: сертификат Let's Encrypt живёт 90 дней.

---

## Если что-то не работает

**`SECRET_KEY variable is not set`** — не заполнен `.env`. Compose
отказывается запускаться без ключа намеренно: без него приложение всё
равно не стартует, а так причина видна сразу, а не в журнале.

**Контейнер `web` перезапускается по кругу.** Смотрите журнал:

```bash
docker compose logs web | tail -50
```

Частые причины: не задан `SECRET_KEY`, пароль базы не совпадает с тем, что
записан в томе, нет связи с `db`.

**`Bad Request (400)`** — адрес, по которому вы открыли систему, не указан
в `ALLOWED_HOSTS`. Впишите и перезапустите: `docker compose up -d`.

**Страницы без стилей** — не собралась статика. Пересоберите:
`docker compose up -d --build`.

**Витрина без фотографий** — проверьте, что каталог `./media` существует и
в нём есть файлы. Он подключается томом и к `web`, и к `nginx`.

**`port is already allocated`** — 80-й порт занят другой программой.
Задайте другой в `.env`:

```ini
HTTP_PORT=8080
```

**Пароль базы поменяли, а `web` не подключается.** Пароль записан в томе
при первом запуске и задним числом не меняется. Либо смените его внутри
PostgreSQL:

```bash
docker compose exec db psql -U warehouse_user -d warehouse_db \
  -c "ALTER USER warehouse_user WITH PASSWORD 'новый_пароль';"
```

либо начните с чистой базы (**все данные пропадут**):

```bash
docker compose down -v
docker compose up -d --build
```

**Не хватает места на диске.** Уберите неиспользуемые образы:

```bash
docker system prune -a
```

Тома с данными эта команда не трогает, но удаляет все образы, которые
сейчас не используются.

---

## Полная очистка

Останавливает всё и **удаляет базу вместе с данными**:

```bash
docker compose down -v
```

Каталоги `./media` и `./logs` при этом остаются — они на диске хоста.
