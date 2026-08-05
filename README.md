# Система складского учёта ООО «ЛЕКО»

Веб-приложение для автоматизации учёта остатков сырья и готовой продукции. 
Разработано на Python (Django), Django REST Framework и PostgreSQL.

## Описание

Система включает:
- **Управление справочниками**: материалы, готовая продукция, поставщики, склады
- **Приходные документы**: регистрация поступления товаров с расчётом остатков
- **Расходные документы**: отпуск товаров с проверкой наличия на складе
- **Инвентаризацию**: проведение переучёта с выявлением недостач и излишков
- **Пять видов отчётов**: остатки, движение, перечень закупок, результаты инвентаризаций
- **REST API**: для интеграции и мобильных приложений
- **Разграничение прав**: роли администратора, кладовщика, экономиста, менеджера

Соответствует техническому заданию и ER-диаграмме из отчёта о практике.

> **Используете Windows?** Команды ниже даны для Linux/macOS (bash).
> Полное руководство для Windows (PowerShell), включая активацию
> виртуального окружения, работу с API и решение частых проблем —
> в файле **`WINDOWS_SETUP.md`**.

## Требования

- Python 3.9+
- pip
- SQLite (встроена в Python, используется по умолчанию)
- PostgreSQL 13+ (для боевой эксплуатации, опционально)

## Быстрый старт

### 1. Перейти в папку проекта

```bash
cd warehouse_project
```

### 2. Создать и активировать виртуальное окружение (рекомендуется)

```bash
python3 -m venv venv          # Linux/macOS: python3, Windows: python
source venv/bin/activate      # Linux/macOS
# .\venv\Scripts\Activate.ps1   # Windows (PowerShell) — см. WINDOWS_SETUP.md
```

### 3. Установить зависимости

```bash
pip install -r requirements.txt
```

### 4. Настроить переменные окружения

```bash
cp .env.example .env
# Отредактировать .env по необходимости, если нужно
```

По умолчанию используется SQLite, проект работает сразу после установки.

### 5. Создать миграции и БД

```bash
python manage.py makemigrations accounts warehouse inventory reports
python manage.py migrate
```

**Важно:** первая команда обязательна — в проекте нет заранее
сгенерированных файлов миграций, `makemigrations` создаёт их из моделей
перед тем как `migrate` применит их к базе данных.

### 6. Создать суперпользователя (администратор)

```bash
python manage.py createsuperuser
# Введите: имя пользователя, email, пароль
```

### 7. Запустить разработчик-сервер

```bash
python manage.py runserver
```

Открыть в браузере: **http://127.0.0.1:8000**

## Доступ

- **Основной интерфейс**: http://127.0.0.1:8000
  - Логин: учетная запись, созданная на шаге 4
  
- **REST API**: http://127.0.0.1:8000/api/
  - Список материалов: `/api/materials/`
  - Приходные документы: `/api/inbound-documents/`
  - Расходные документы: `/api/outbound-documents/`
  - Остатки: `/api/stock/`
  - Инвентаризации: `/api/inventories/`
  - Движение товаров: `/api/movements/`
  
- **Админ-панель**: http://127.0.0.1:8000/admin
  - Полный доступ для суперпользователя

## Использование PostgreSQL

Для переключения с SQLite на PostgreSQL (рекомендуется для боевой эксплуатации):

### 1. Установить psycopg2 (уже в requirements.txt)

### 2. Создать БД на PostgreSQL

**Linux/macOS:**

```bash
createdb warehouse_db
createuser warehouse_user
# Задать пароль для warehouse_user
```

**Windows:** те же команды `createdb`/`createuser` работают, если папка
`bin` установки PostgreSQL добавлена в PATH (инсталлятор с python.org
предлагает это автоматически). Более простой вариант для Windows —
использовать **pgAdmin** (устанавливается вместе с PostgreSQL): открыть
pgAdmin → правой кнопкой на «Databases» → Create → Database (имя
`warehouse_db`), затем на «Login/Group Roles» → Create → Login/Group
Role (имя `warehouse_user`, вкладка Definition — задать пароль, вкладка
Privileges — включить «Can login?»).

### 3. Отредактировать .env

```bash
DB_ENGINE=postgres
DB_NAME=warehouse_db
DB_USER=warehouse_user
DB_PASSWORD=ваш_пароль
DB_HOST=localhost
DB_PORT=5432
```

### 4. Пересоздать миграции

```bash
python manage.py migrate
```

## Примеры использования API

### Создание материала

```bash
curl -X POST http://127.0.0.1:8000/api/materials/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Ткань хлопчатобумажная",
    "unit": "m",
    "category": "textile",
    "reorder_point": 50
  }'
```

### Получение списка приходных документов

```bash
curl http://127.0.0.1:8000/api/inbound-documents/ \
  -H "Accept: application/json"
```

### Проведение приходного документа

```bash
curl -X POST http://127.0.0.1:8000/api/inbound-documents/1/process/ \
  -H "Content-Type: application/json" \
  -d '{}'
```

## Структура проекта

```
warehouse_project/
├── warehouse_config/       # Конфигурация проекта
│   ├── settings.py         # Настройки Django
│   ├── urls.py             # Маршруты (API + страницы)
│   └── wsgi.py
├── accounts/               # Приложение пользователей
│   └── models.py           # Кастомная модель User с ролями
├── warehouse/              # Основное приложение (склад)
│   ├── models.py           # Модели: Material, Product, Stock, Document и т. д.
│   ├── views.py            # ViewSet-ы REST API
│   ├── serializers.py      # Сериализаторы для JSON
│   ├── services.py         # Бизнес-логика (процесс документов)
│   ├── signals.py          # Сигналы (уведомления о низком остатке)
│   └── tests.py            # Тесты
├── inventory/              # Приложение инвентаризации
│   ├── models.py           # Inventory, InventoryItem
│   ├── views.py            # ViewSet инвентаризаций
│   └── services.py         # Логика проведения инвентаризации
├── reports/                # Приложение отчётов
│   └── views.py            # Четыре вида отчётов + экспорт Excel
├── templates/              # HTML шаблоны
│   ├── base.html           # Базовый шаблон с меню
│   ├── login.html          # Страница входа
│   └── *.html              # Страницы интерфейса
├── static/                 # CSS, JS, изображения
├── manage.py               # Утилита управления Django
├── requirements.txt        # Список зависимостей
└── .env.example           # Пример переменных окружения
```

## Основные модели

| Таблица | Назначение |
|---------|-----------|
| `Material` | Материалы и сырьё |
| `Product` | Готовая продукция (с учётом размера и цвета) |
| `Warehouse` | Склады |
| `Supplier` | Поставщики |
| `InboundDocument` + `InboundItem` | Приходные документы |
| `OutboundDocument` + `OutboundItem` | Расходные документы |
| `Stock` | Текущие остатки (обновляются при проведении документов) |
| `StockMovement` | Журнал всех движений товаров |
| `PriceHistory` | История закупочных цен |
| `Inventory` + `InventoryItem` | Инвентаризационные описи |
| `User` | Пользователи с ролями (администратор, кладовщик, экономист, менеджер) |

## Роли и права доступа

- **Администратор**: полный доступ, управление пользователями
- **Кладовщик**: создание и проведение документов прихода/расхода
- **Экономист**: просмотр отчётов, управление справочниками
- **Менеджер**: просмотр сводок и отчётов, контроль остатков

## Тестирование

```bash
# Запустить все тесты
python manage.py test

# Тесты конкретного приложения
python manage.py test warehouse
python manage.py test inventory

# С выводом времени выполнения
python manage.py test --verbosity=2
```

## Уведомления

При падении остатка материала ниже минимума отправляется письмо (в режиме разработки выводится в консоль).
При завершении инвентаризации с выявленными недостачами отправляется отчёт.

Для настройки реальной почты отредактируйте EMAIL_BACKEND в settings.py.

## Документация

Полная техническая документация находится в файле отчёта о практике (отчет.docx):
- п. 1.3 — Обоснование выбора технологий
- п. 2.1 — Проектирование ER-диаграммы
- п. 2.2 — Разработка моделей, API, тесты
- п. 2.3 — Оптимизация запросов и результаты тестирования

## Развёртывание в боевом режиме

1. Переключиться на PostgreSQL (см. выше)
2. Установить SECRET_KEY в .env (сложный случайный ключ)
3. Установить DEBUG=False в .env
4. Собрать статические файлы: `python manage.py collectstatic`
5. Использовать WSGI-сервер (gunicorn, uWSGI и т. п.)
6. Настроить nginx как reverse proxy
7. Включить HTTPS

Пример запуска с gunicorn:
```bash
gunicorn warehouse_config.wsgi --bind 0.0.0.0:8000 --workers 4
```

## Лицензия

Учебный проект. Разработано в рамках производственной практики.

## Контакты

При возникновении вопросов по коду обратитесь к автору.
