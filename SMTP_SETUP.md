# Настройка Email уведомлений

## Система уведомлений

Приложение отправляет письма в следующих случаях:

1. **Низкий остаток материала** — когда остаток падает ниже минимума (сигнал `/warehouse/signals.py`)
2. **Недостачи при инвентаризации** — при выявлении существенных недостач (сигнал `/inventory/services.py`)

## Режим разработки (по умолчанию)

При `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` письма выводятся в консоль где запущен `python manage.py runserver`:

```
Content-Type: text/plain; charset="utf-8"
MIME-Version: 1.0
Content-Transfer-Encoding: 7bit
Subject: Низкий остаток: Ткань хлопок
From: warehouse@leko.example
To: manager@leko.example
Date: Wed, 30 Jul 2026 15:30:45 -0000

Остаток «Ткань хлопок» составляет 5 м — ниже минимума 50. Требуется закупка.
```

Удобно для тестирования в процессе разработки!

---

## Production: Настройка с Gmail

Самый простой вариант для небольших предприятий.

### Шаг 1: Создать App Password

1. Перейти на https://myaccount.google.com
2. В левом меню выбрать **Безопасность**
3. Убедиться, что **двухфакторная аутентификация** включена
4. Внизу страницы найти **App passwords** (или перейти https://myaccount.google.com/apppasswords)
5. Выбрать **Mail** и **Windows Computer** (или другое)
6. Google выдаст 16-символьный пароль, например: `xyza bcde fghi jklm`

### Шаг 2: Настроить .env

```bash
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=warehouse@gmail.com
EMAIL_HOST_PASSWORD=xyza bcde fghi jklm
DEFAULT_FROM_EMAIL=warehouse@leko.example
WAREHOUSE_MANAGER_EMAIL=manager@leko.example
```

### Шаг 3: Протестировать

```bash
python manage.py shell
```

```python
from django.core.mail import send_mail
send_mail(
    'Тест',
    'Это тестовое письмо из системы складского учёта',
    'warehouse@leko.example',
    ['manager@leko.example'],
    fail_silently=False,
)
```

Если письмо пришло — всё работает! ✅

---

## Production: Яндекс.Почта

### Шаг 1: Создать пароль приложения

1. Перейти на https://passport.yandex.ru
2. Открыть **Безопасность** → **Пароли и авторизация**
3. Создать пароль приложения для **Почта**
4. Скопировать пароль

### Шаг 2: Настроить .env

```bash
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.yandex.ru
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=warehouse@yandex.ru
EMAIL_HOST_PASSWORD=полученный-пароль-приложения
DEFAULT_FROM_EMAIL=warehouse@leko.example
WAREHOUSE_MANAGER_EMAIL=manager@leko.example
```

---

## Production: SendGrid (рекомендуется для высоконагруженных систем)

### Шаг 1: Создать аккаунт

1. Зарегистрироваться на https://sendgrid.com
2. Получить API ключ

### Шаг 2: Установить пакет

```bash
pip install sendgrid-django
```

### Шаг 3: Настроить .env

```bash
EMAIL_BACKEND=sendgrid_backend.SendgridBackend
SENDGRID_API_KEY=SG.xxx...
DEFAULT_FROM_EMAIL=warehouse@leko.example
WAREHOUSE_MANAGER_EMAIL=manager@leko.example
```

### Шаг 4: Обновить settings.py (если нужен)

Добавить в `INSTALLED_APPS`:
```python
'sendgrid_backend',
```

---

## Собственный SMTP сервер

Если на предприятии есть свой почтовый сервер:

```bash
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=mail.leko.ru
EMAIL_PORT=465
EMAIL_USE_SSL=True
EMAIL_HOST_USER=warehouse@leko.ru
EMAIL_HOST_PASSWORD=secure-password
DEFAULT_FROM_EMAIL=warehouse@leko.ru
WAREHOUSE_MANAGER_EMAIL=manager@leko.ru
```

---

## Тестирование

### 1. Через Django shell

```bash
python manage.py shell
```

```python
from django.core.mail import send_mail

result = send_mail(
    subject='Тест системы складского учёта',
    message='Это тестовое письмо. Если вы его получили, почта работает!',
    from_email='warehouse@leko.example',
    recipient_list=['manager@leko.example'],
    fail_silently=False,
)

print(f"Писем отправлено: {result}")
```

### 2. Через создание низкого остатка

Создайте материал с малым минимумом, затем создайте расходный документ на большое количество:

```python
from warehouse.models import Material, Stock, Warehouse, OutboundDocument, OutboundItem
from warehouse.services import process_outbound_document

# Материал
m = Material.objects.create(name='Тест', unit='pc', category='fittings', reorder_point=100)

# Остаток
w = Warehouse.objects.first()
Stock.objects.create(warehouse=w, material=m, quantity=50)

# Документ расхода
doc = OutboundDocument.objects.create(
    doc_number='Р-TEST', doc_date='2026-07-30',
    warehouse=w, purpose='production'
)
OutboundItem.objects.create(outbound_doc=doc, material=m, quantity=40)

# Проведение (остаток упадёт ниже минимума)
process_outbound_document(doc.pk)

# Должно прийти письмо о низком остатке! 📧
```

---

## Отладка проблем

### Письмо не приходит

1. **Проверить консоль** (если `EMAIL_BACKEND=console`)
   ```bash
   # Письмо должно быть видно в консоли где запущен runserver
   ```

2. **Проверить логи Django**
   ```bash
   # Добавить в settings.py
   LOGGING = {
       'version': 1,
       'disable_existing_loggers': False,
       'handlers': {
           'console': {'class': 'logging.StreamHandler'},
       },
       'loggers': {
           'django.core.mail': {'handlers': ['console'], 'level': 'DEBUG'},
       },
   }
   ```

3. **Протестировать SMTP вручную**
   ```bash
   python manage.py shell
   
   import smtplib
   from email.mime.text import MIMEText
   
   msg = MIMEText('Тест')
   msg['Subject'] = 'Тест'
   msg['From'] = 'warehouse@gmail.com'
   msg['To'] = 'manager@leko.example'
   
   with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
       server.login('warehouse@gmail.com', 'xxxx xxxx xxxx xxxx')
       server.send_message(msg)
   ```

### Ошибка: "SMTPAuthenticationError"

- Неверный пароль или App Password
- Для Gmail используйте **App Password**, а не обычный пароль
- Проверьте включена ли двухфакторная аутентификация

### Ошибка: "SMTPException: 554 5.7.1 Service not available"

- Сервер почты может быть недоступен
- Проверьте интернет-соединение
- Убедитесь что хост и порт верны

---

## Рекомендации

| Сценарий | Рекомендуемое решение |
|----------|----------------------|
| Разработка, тестирование | console backend (письма в консоль) |
| Малое предприятие (< 100 писем/день) | Gmail с App Password |
| Среднее предприятие | Яндекс.Почта или собственный SMTP |
| Высоконагруженная система (> 1000 писем/день) | SendGrid или другой ESP |
| Уже есть почтовый сервер | Собственный SMTP |

---

## Настройка для Docker

При использовании Docker Compose параметры почты передаются через `environment:` в `docker-compose.yml`:

```yaml
web:
  environment:
    EMAIL_BACKEND: django.core.mail.backends.smtp.EmailBackend
    EMAIL_HOST: smtp.gmail.com
    EMAIL_PORT: 587
    EMAIL_USE_TLS: True
    EMAIL_HOST_USER: warehouse@gmail.com
    EMAIL_HOST_PASSWORD: xxxx xxxx xxxx xxxx
    DEFAULT_FROM_EMAIL: warehouse@leko.example
    WAREHOUSE_MANAGER_EMAIL: manager@leko.example
```
