# REST API Примеры

Полная документация REST API системы складского учёта.

> **PowerShell на Windows?** Команды `curl` ниже написаны для
> Linux/macOS (bash). В PowerShell `curl` — это псевдоним другой
> команды с иным синтаксисом. Используйте `curl.exe` вместо `curl`,
> либо нативные команды `Invoke-RestMethod` — примеры обоих вариантов
> смотрите в разделе «Использование API из PowerShell» файла
> `WINDOWS_SETUP.md`.

## Аутентификация

Все запросы требуют аутентификации через session или Basic Auth:

```bash
# Вход в систему (получить session cookie)
curl -X POST http://127.0.0.1:8000/api-auth/login/ \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin123" \
  -c cookies.txt

# Использование session cookie в последующих запросах
curl http://127.0.0.1:8000/api/materials/ -b cookies.txt
```

Или используйте Basic Auth:

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/materials/
```

---

## Материалы

### Список материалов

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/materials/
```

С фильтрацией:

```bash
# По категории
curl -u admin:admin123 "http://127.0.0.1:8000/api/materials/?category=textile"

# По единице измерения
curl -u admin:admin123 "http://127.0.0.1:8000/api/materials/?unit=kg"

# Поиск по названию
curl -u admin:admin123 "http://127.0.0.1:8000/api/materials/?search=ткань"

# Постранично (по 25 позиций по умолчанию)
curl -u admin:admin123 "http://127.0.0.1:8000/api/materials/?page=2"
```

### Создание материала

```bash
curl -X POST http://127.0.0.1:8000/api/materials/ \
  -u admin:admin123 \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Хлопчатобумажная ткань",
    "unit": "m",
    "category": "textile",
    "reorder_point": 50.0,
    "description": "Натуральная хлопчатобумажная ткань для пошива"
  }'
```

### Получение материала по ID

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/materials/1/
```

### Обновление материала

```bash
curl -X PUT http://127.0.0.1:8000/api/materials/1/ \
  -u admin:admin123 \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Хлопок (обновлено)",
    "reorder_point": 75.0
  }'
```

### Удаление материала

```bash
curl -X DELETE http://127.0.0.1:8000/api/materials/1/ \
  -u admin:admin123
```

### Материалы с низким остатком

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/materials/low_stock/
```

---

## Готовая продукция

### Список продукции

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/products/

# С фильтрацией
curl -u admin:admin123 "http://127.0.0.1:8000/api/products/?category=shoes&size=42"
```

### Создание продукции

```bash
curl -X POST http://127.0.0.1:8000/api/products/ \
  -u admin:admin123 \
  -H "Content-Type: application/json" \
  -d '{
    "article_number": "SH-001-42-BLK",
    "name": "Спортивные кроссовки",
    "category": "shoes",
    "size": "42",
    "color": "Чёрный",
    "cost": 1500.00,
    "selling_price": 2990.00,
    "status": "active"
  }'
```

---

## Приходные документы

### Список приходов

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/inbound-documents/

# Только не проведённые
curl -u admin:admin123 "http://127.0.0.1:8000/api/inbound-documents/?processed=false"

# По складу
curl -u admin:admin123 "http://127.0.0.1:8000/api/inbound-documents/?warehouse=1"
```

### Создание приходного документа

```bash
curl -X POST http://127.0.0.1:8000/api/inbound-documents/ \
  -u admin:admin123 \
  -H "Content-Type: application/json" \
  -d '{
    "doc_number": "П-2026-001",
    "doc_date": "2026-07-30",
    "supplier": 1,
    "warehouse": 1,
    "items": [
      {
        "material": 1,
        "quantity": 100.0,
        "unit_price": 250.00,
        "batch_number": "BATCH-001",
        "expiry_date": null
      },
      {
        "product": 2,
        "quantity": 50.0,
        "unit_price": 2000.00,
        "batch_number": "LOT-2026-001"
      }
    ]
  }'
```

### Проведение приходного документа (главное!)

После создания документа его нужно провести, чтобы остатки обновились:

```bash
curl -X POST http://127.0.0.1:8000/api/inbound-documents/1/process/ \
  -u admin:admin123 \
  -H "Content-Type: application/json" \
  -d '{}'
```

При проведении:
- Остатки товаров обновляются
- Создаётся запись в журнале движения (StockMovement)
- История закупочных цен сохраняется

---

## Расходные документы

### Список расходов

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/outbound-documents/

# Только проведённые
curl -u admin:admin123 "http://127.0.0.1:8000/api/outbound-documents/?processed=true"
```

### Создание расходного документа

```bash
curl -X POST http://127.0.0.1:8000/api/outbound-documents/ \
  -u admin:admin123 \
  -H "Content-Type: application/json" \
  -d '{
    "doc_number": "Р-2026-001",
    "doc_date": "2026-07-30",
    "warehouse": 1,
    "purpose": "production",
    "production_order": "ПЗ-001",
    "items": [
      {
        "material": 1,
        "quantity": 25.0,
        "unit_price": 250.00
      }
    ]
  }'
```

### Проведение (с проверкой остатков!)

```bash
curl -X POST http://127.0.0.1:8000/api/outbound-documents/1/process/ \
  -u admin:admin123 \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Важно:** Если на складе недостаточно товара, будет ошибка 400:

```json
{"error": "Недостаточно товара: остаток 10, требуется 20"}
```

---

## Остатки на складе

### Текущие остатки

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/stock/

# По складу
curl -u admin:admin123 "http://127.0.0.1:8000/api/stock/?warehouse=1"

# По материалу
curl -u admin:admin123 "http://127.0.0.1:8000/api/stock/?material=5"
```

---

## Журнал движения товаров

### История операций

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/movements/

# По типу (in/out/adjust)
curl -u admin:admin123 "http://127.0.0.1:8000/api/movements/?movement_type=in"

# За период (требуется фильтрация на клиенте)
curl -u admin:admin123 http://127.0.0.1:8000/api/movements/
```

---

## Инвентаризация

### Список инвентаризаций

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/inventories/

# Только завершённые
curl -u admin:admin123 "http://127.0.0.1:8000/api/inventories/?status=completed"
```

### Создание инвентаризации

```bash
curl -X POST http://127.0.0.1:8000/api/inventories/ \
  -u admin:admin123 \
  -H "Content-Type: application/json" \
  -d '{
    "number": "ИНВ-001",
    "warehouse": 1
  }'
```

### Заполнение описи текущими остатками

```bash
curl -X POST http://127.0.0.1:8000/api/inventories/1/build_sheet/ \
  -u admin:admin123 \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Внесение фактических остатков

```bash
curl -X POST http://127.0.0.1:8000/api/inventories/1/save_counts/ \
  -u admin:admin123 \
  -H "Content-Type: application/json" \
  -d '{
    "counts": [
      {"item_id": 1, "actual_quantity": 95},
      {"item_id": 2, "actual_quantity": 50},
      {"item_id": 3, "actual_quantity": 102}
    ]
  }'
```

### Завершение инвентаризации (расчёт расхождений)

```bash
curl -X POST http://127.0.0.1:8000/api/inventories/1/finalize/ \
  -u admin:admin123 \
  -H "Content-Type: application/json" \
  -d '{}'
```

Ответ содержит выявленные недостачи и излишки:

```json
{
  "status": "ok",
  "discrepancies": [
    {"item": "Ткань", "system": 100, "actual": 95, "difference": -5},
    {"item": "Пуговицы", "system": 50, "actual": 52, "difference": 2}
  ]
}
```

---

## Отчёты

### Остатки на дату

```bash
curl -u admin:admin123 "http://127.0.0.1:8000/api/reports/stock/?warehouse=1"
```

### Движение товаров за период

```bash
curl -u admin:admin123 "http://127.0.0.1:8000/api/reports/movement/?days=30"
```

### Позиции для закупки

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/reports/reorder/
```

### Экспорт остатков в Excel

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/reports/stock/export/ \
  > остатки.xlsx
```

### Главная панель (сводка)

```bash
curl -u admin:admin123 http://127.0.0.1:8000/api/dashboard/
```

---

## Python примеры

### Установка requests

```bash
pip install requests
```

### Вход и работа с API

```python
import requests
import json

# Базовый URL и учётные данные
BASE_URL = 'http://127.0.0.1:8000'
USERNAME = 'admin'
PASSWORD = 'admin123'

# Создать сессию для хранения cookies
session = requests.Session()
session.auth = (USERNAME, PASSWORD)

# Получить список материалов
response = session.get(f'{BASE_URL}/api/materials/')
materials = response.json()
print(json.dumps(materials, indent=2, ensure_ascii=False))

# Создать новый материал
new_material = {
    'name': 'Шерстяная ткань',
    'unit': 'm',
    'category': 'textile',
    'reorder_point': 30.0,
    'description': 'Высокогорная шерсть'
}
response = session.post(f'{BASE_URL}/api/materials/', json=new_material)
print(response.status_code, response.json())

# Получить приходные документы
response = session.get(f'{BASE_URL}/api/inbound-documents/')
docs = response.json()
for doc in docs['results']:
    print(f"Документ {doc['doc_number']}: {doc['total_sum']} руб.")

# Провести приходный документ
response = session.post(f'{BASE_URL}/api/inbound-documents/1/process/')
print(response.json())

# Получить остатки на складе
response = session.get(f'{BASE_URL}/api/stock/?warehouse=1')
stocks = response.json()
print(f"Найдено позиций: {len(stocks['results'])}")
```

### Полный цикл прихода

```python
def full_inbound_cycle(session, doc_number, warehouse_id, supplier_id, items):
    """Полный цикл: создание документа → проведение → проверка остатков"""
    
    BASE_URL = 'http://127.0.0.1:8000'
    
    # 1. Создать документ
    doc_data = {
        'doc_number': doc_number,
        'doc_date': '2026-07-30',
        'warehouse': warehouse_id,
        'supplier': supplier_id,
        'items': items
    }
    response = session.post(f'{BASE_URL}/api/inbound-documents/', json=doc_data)
    doc_id = response.json()['id']
    print(f"✓ Документ {doc_number} создан (ID={doc_id})")
    
    # 2. Провести документ
    response = session.post(f'{BASE_URL}/api/inbound-documents/{doc_id}/process/')
    print(f"✓ Документ проведён: {response.json()}")
    
    # 3. Проверить остатки
    response = session.get(f'{BASE_URL}/api/stock/?warehouse={warehouse_id}')
    stocks = response.json()
    for stock in stocks['results']:
        print(f"  {stock['item_name']}: {stock['quantity']} {stock['unit']}")

# Пример использования
items = [
    {'material': 1, 'quantity': 100, 'unit_price': 250, 'batch_number': 'BATCH-1'},
    {'product': 2, 'quantity': 50, 'unit_price': 2000}
]
full_inbound_cycle(session, 'П-2026-100', warehouse_id=1, supplier_id=1, items=items)
```

---

## Обработка ошибок

Код ошибки 400 (неверные данные):

```json
{"detail": "Укажите материал или продукцию, но не оба"}
```

Код 401 (не аутентифицирован):

```json
{"detail": "Authentication credentials were not provided."}
```

Код 404 (не найдено):

```json
{"detail": "Not found."}
```

Код 500 (внутренняя ошибка):

```json
{"detail": "Server error"}
```

---

## Лучшие практики

1. **Используйте сессии** для хранения cookies между запросами
2. **Проверяйте response.status_code** перед использованием данных
3. **Ловите исключения** при работе с сетью
4. **Не храните пароли** в коде — используйте .env переменные
5. **Логируйте все операции** с документами для аудита
6. **Используйте транзакции** при создании связанных объектов
