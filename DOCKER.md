# Docker развёртывание

Полное руководство по запуску приложения в контейнерах.

## Требования

- Docker 20.10+
- Docker Compose 2.0+ (опционально, но рекомендуется)

Установка:
- [Docker Desktop для Windows/Mac](https://www.docker.com/products/docker-desktop)
- [Docker для Linux](https://docs.docker.com/engine/install/)

---

## Быстрый старт с Docker Compose (рекомендуется)

### 1. Подготовка

```bash
cd warehouse_project

# Скопировать и настроить окружение
cp .env.example .env

# Отредактировать .env если нужно изменить пароли БД
```

### 2. Запуск

```bash
# Запустить все сервисы (БД, приложение, nginx)
docker-compose up -d

# Ждать примерно 10-15 секунд (БД инициализируется)
docker-compose logs -f web  # Наблюдать логи приложения

# Когда увидите "Quit the server with CONTROL-C"
# Приложение готово к работе
```

### 3. Первый запуск

```bash
# Создать суперпользователя
docker-compose exec web python manage.py createsuperuser

# Загрузить тестовые данные (опционально)
docker-compose exec web python manage.py loaddata fixtures.json
```

### 4. Доступ

- **Веб-интерфейс**: http://localhost (или http://127.0.0.1)
- **API**: http://localhost/api/
- **Админ-панель**: http://localhost/admin/

### 5. Остановка

```bash
docker-compose down

# С удалением объёма БД (осторожно — потеря данных!)
docker-compose down -v
```

---

## Docker Compose состав

`docker-compose.yml` запускает три сервиса:

1. **PostgreSQL** (порт 5432)
   - Хранит все данные
   - Создаёт БД `warehouse_db` автоматически
   - Данные сохраняются в volume `postgres_data`

2. **Django приложение** (порт 8000 внутри, 80 на хосте)
   - Выполняет миграции БД при старте
   - Собирает статические файлы
   - Запускается с Gunicorn (4 воркера)

3. **Nginx** (порт 80)
   - Reverse proxy к приложению
   - Служит статические файлы
   - Обрабатывает HTTPS (если настроен)

---

## Запуск отдельно без Compose (для опытных)

### 1. Запустить PostgreSQL

```bash
docker run -d \
  --name warehouse-db \
  -e POSTGRES_DB=warehouse_db \
  -e POSTGRES_USER=warehouse_user \
  -e POSTGRES_PASSWORD=secure_password \
  -v postgres_data:/var/lib/postgresql/data \
  -p 5432:5432 \
  postgres:15-alpine
```

### 2. Собрать образ приложения

```bash
docker build -t warehouse-app .
```

### 3. Запустить приложение

```bash
docker run -d \
  --name warehouse-web \
  --link warehouse-db:db \
  -e DB_ENGINE=postgres \
  -e DB_HOST=db \
  -e DB_NAME=warehouse_db \
  -e DB_USER=warehouse_user \
  -e DB_PASSWORD=secure_password \
  -p 8000:8000 \
  warehouse-app
```

### 4. Выполнить миграции

```bash
docker exec warehouse-web python manage.py migrate
```

---

## Полезные команды

### Логи

```bash
# Логи всех сервисов
docker-compose logs -f

# Логи только приложения
docker-compose logs -f web

# Логи БД
docker-compose logs -f db

# Последние 100 строк
docker-compose logs --tail=100 web
```

### Выполнить команды

```bash
# Django команды
docker-compose exec web python manage.py createsuperuser
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py collectstatic
docker-compose exec web python manage.py shell

# Bash в контейнере
docker-compose exec web bash

# SQL запросы
docker-compose exec db psql -U warehouse_user -d warehouse_db
```

### Очистка

```bash
# Остановить все контейнеры
docker-compose down

# Удалить объёмы (потеря данных!)
docker-compose down -v

# Пересобрать образ
docker-compose build --no-cache

# Перезапустить приложение
docker-compose restart web
```

---

## Переменные окружения

При использовании `docker-compose up` переменные из `.env` автоматически загружаются.

Основные переменные можно переопределить через `environment:` в `docker-compose.yml`:

```yaml
web:
  environment:
    DEBUG: "False"
    SECRET_KEY: "your-secret-key"
    DB_PASSWORD: "strong-password"
    EMAIL_HOST: smtp.gmail.com
    EMAIL_HOST_PASSWORD: "app-password"
```

---

## Production: HTTPS и домены

### 1. Получить сертификат Let's Encrypt

```bash
# Установить Certbot
sudo apt-get install certbot python3-certbot-nginx

# Создать сертификат для warehouse.example.com
sudo certbot certonly --standalone -d warehouse.example.com

# Сертификаты в /etc/letsencrypt/live/warehouse.example.com/
```

### 2. Обновить nginx.conf

```nginx
server {
    listen 443 ssl;
    server_name warehouse.example.com;

    ssl_certificate /etc/letsencrypt/live/warehouse.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/warehouse.example.com/privkey.pem;

    # ... остальная конфигурация ...
}

# Редирект с HTTP на HTTPS
server {
    listen 80;
    server_name warehouse.example.com;
    return 301 https://$server_name$request_uri;
}
```

### 3. Обновить docker-compose.yml

```yaml
services:
  nginx:
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro  # Монтировать сертификаты
```

### 4. Автообновление сертификатов

```bash
# Обновить сертификат
sudo certbot renew --dry-run

# Перезагрузить nginx в контейнере
docker-compose exec nginx nginx -s reload
```

---

## Резервное копирование

### Резервная копия БД

```bash
# Экспорт в SQL файл
docker-compose exec db pg_dump -U warehouse_user warehouse_db > backup.sql

# Восстановление
docker-compose exec -T db psql -U warehouse_user warehouse_db < backup.sql
```

### Резервная копия статических файлов и медиа

```bash
# Если есть media files
docker-compose exec web tar -czf backup.tar.gz /app/mediafiles /app/staticfiles

# Скопировать на хост
docker cp warehouse-web:/app/backup.tar.gz ./backup.tar.gz
```

---

## Мониторинг

### Проверка здоровья

```bash
# Endpoint /health должен возвращать 200
curl http://localhost/health

# Метрики Django (если установлено)
curl http://localhost/api/dashboard/
```

### Размер контейнеров

```bash
# Размер образов
docker images | grep warehouse

# Размер контейнеров
docker ps -s
```

### Использование ресурсов

```bash
# CPU, память, сеть
docker stats warehouse-web warehouse-db
```

---

## Масштабирование

### Несколько woркеров приложения

```yaml
web:
  deploy:
    replicas: 3  # 3 экземпляра приложения
```

Или запустить отдельные контейнеры:

```bash
docker-compose up -d --scale web=3
```

### Load balancing через nginx

Nginx (в docker-compose.yml) автоматически распределяет нагрузку между несколькими экземплярами.

---

## Troubleshooting

### Ошибка: "database connection refused"

Обычно БД ещё не готова при старте приложения.

```bash
# Убедиться что БД запущена
docker-compose logs db

# Перезапустить с задержкой
docker-compose restart web
docker-compose exec web python manage.py migrate
```

### Ошибка: "permission denied" при доступе к файлам

```bash
# Проверить права в контейнере
docker-compose exec web ls -la /app

# Изменить владельца файлов
docker-compose exec web chown -R www-data:www-data /app
```

### Приложение медленное или падает

```bash
# Увеличить workers в docker-compose.yml
command: ["gunicorn", "warehouse_config.wsgi:application", 
          "--bind", "0.0.0.0:8000", "--workers", "8"]

# Увеличить память контейнера
deploy:
  resources:
    limits:
      memory: 2G
```

### Очистить всё и начать заново

```bash
# Остановить и удалить всё
docker-compose down -v

# Пересобрать
docker-compose build --no-cache

# Запустить с нуля
docker-compose up -d
docker-compose exec web python manage.py createsuperuser
```

---

## Файлы для сохранения между запусками

При использовании Docker важно сохранять:

1. **Volume PostgreSQL** (`postgres_data`) — хранит БД
2. **Volume статических файлов** (если нужно сохранить)
3. **Конфигурация** (.env файл)

В `docker-compose.yml` это автоматически сделано через `volumes:`.
