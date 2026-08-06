# Установка и запуск на Windows

Это руководство — Windows-версия README.md. Весь Python/Django-код в проекте
кроссплатформенный (использует `pathlib`, работает одинаково на Windows,
Linux и macOS) — отличаются только команды в терминале.

## Требования

- **Python 3.10 и новее** (проверено вплоть до 3.14) — скачать с https://www.python.org/downloads/
  При установке **обязательно** отметьте галочку **«Add python.exe to PATH»**
  на первом экране инсталлятора — без неё команда `python` не будет найдена.
- **PowerShell** (уже есть в Windows 10/11) — рекомендуется вместо
  Командной строки (cmd), команды ниже даны для PowerShell.
- Docker Desktop — опционально, если хотите запускать через Docker.

### Проверка, что Python установлен правильно

Откройте **PowerShell** (Пуск → введите «PowerShell» → Enter) и выполните:

```powershell
python --version
```

Должно вывести что-то вроде `Python 3.11.5`. Если вместо этого:
- открылся **Microsoft Store** — Python не установлен, App Execution Alias
  перехватывает команду. Установите Python с python.org, либо отключите
  алиас: **Параметры → Приложения → Дополнительные параметры приложений →
  Псевдонимы выполнения приложений** → выключите оба переключателя python.
- ошибка **«python не является внутренней или внешней командой»** —
  Python не добавлен в PATH. Переустановите с галочкой «Add to PATH», или
  используйте `py` вместо `python` (лаунчер `py.exe` ставится отдельно и
  часто работает, даже если PATH не настроен для `python.exe`).

## Быстрый старт

### 1. Распаковать архив

Если Проводник Windows не открывает `.tar.gz` через «Извлечь всё» (эта
кнопка понимает только `.zip`) — используйте один из вариантов:

**Вариант А — если у вас `warehouse_project.zip`:**
Просто щёлкните правой кнопкой → «Извлечь всё...».

**Вариант Б — если только `warehouse_project.tar.gz`:**
В PowerShell (начиная с Windows 10 версии 1803+ `tar` встроен):

```powershell
tar -xzf warehouse_project.tar.gz
```

### 2. Перейти в папку проекта

```powershell
cd warehouse_project
```

### 3. Создать виртуальное окружение

```powershell
python -m venv venv
```

### 4. Активировать виртуальное окружение

```powershell
.\venv\Scripts\Activate.ps1
```

**Если появилась ошибка о запрете выполнения скриптов** («...cannot be
loaded because running scripts is disabled on this system»), выполните
один раз (нужны права на изменение политики для текущего пользователя,
администратор не требуется):

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Подтвердите `Y`, затем повторите шаг 4. Строка приглашения должна
измениться на `(venv) PS ...>` — это значит, что окружение активно.

### 5. Установить зависимости

```powershell
pip install -r requirements.txt
```

### 6. Создать базу данных

```powershell
python manage.py migrate
```

Миграции лежат в репозитории, `makemigrations` запускать не нужно —
`migrate` создаст все таблицы сам.

### 7. Создать администратора

```powershell
python manage.py createsuperuser
```

Введите имя пользователя, email (можно пропустить — Enter) и пароль.
Пароль не отображается при вводе — это нормально, просто печатайте и
нажимайте Enter.

### 8. Запустить сервер

```powershell
python manage.py runserver
```

**Если Windows Defender Firewall покажет всплывающее окно** «Разрешить
доступ Python через брандмауэр?» — нажмите **«Разрешить доступ»**
(нужно для работы сервера в локальной сети; для доступа только с этого же
компьютера через `127.0.0.1` можно нажать «Отмена», сервер всё равно
заработает).

### 9. Открыть в браузере

```
http://127.0.0.1:8000
```

Остановить сервер: `Ctrl+C` в окне PowerShell.

---

## Каждый следующий запуск

Виртуальное окружение нужно активировать заново в каждом новом окне
PowerShell (оно не запоминается между сессиями):

```powershell
cd warehouse_project
.\venv\Scripts\Activate.ps1
python manage.py runserver
```

---

## Запуск через Docker Desktop

Если у вас установлен **Docker Desktop для Windows** (с включённым
бэкендом WSL2 — это предлагается по умолчанию при установке):

```powershell
cd warehouse_project
docker-compose up -d
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser
```

Открыть в браузере: `http://localhost`

Остальные Docker-команды (логи, остановка и т. д.) — без изменений,
смотрите `DOCKER.md`, они одинаковы что на Windows, что на Linux/macOS,
поскольку Docker Desktop сам абстрагирует различия ОС.

Проверить, что Docker работает:

```powershell
docker --version
docker-compose --version
```

---

## Использование API из PowerShell

В примерах `API_EXAMPLES.md` команды даны для `curl` в стиле Linux/macOS.
В PowerShell есть важный нюанс: команда `curl` там — это **псевдоним**
для `Invoke-WebRequest`, который принимает параметры иначе, чем настоящий
curl. Есть два способа получить те же результаты:

### Способ 1 — вызвать настоящий curl.exe напрямую

Начиная с Windows 10 версии 1803, в системе есть реальный `curl.exe`.
Чтобы обратиться именно к нему (в обход алиаса PowerShell), пишите
`curl.exe` вместо `curl`:

```powershell
curl.exe -u admin:admin123 http://127.0.0.1:8000/api/materials/
```

### Способ 2 — нативные команды PowerShell

```powershell
# Список материалов
$cred = Get-Credential   # введите admin / пароль в окне
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/materials/" -Credential $cred

# Создание материала
$body = @{
    name = "Хлопчатобумажная ткань"
    unit = "m"
    category = "textile"
    reorder_point = 50.0
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/materials/" `
    -Method Post -Credential $cred `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($body))

# Проведение приходного документа
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/inbound-documents/1/process/" `
    -Method Post -Credential $cred -ContentType "application/json" -Body "{}"
```

**Примечание про кириллицу:** при отправке текста на русском языке через
`Invoke-RestMethod` рекомендуется явно кодировать тело запроса в UTF-8
байты (как показано выше через `[System.Text.Encoding]::UTF8.GetBytes(...)`),
иначе PowerShell 5.1 может отправить его в неверной кодировке.

---
## Обновление работающей системы

Обновляется только код. База, фотографии товаров и файл настроек в
репозитории не хранятся и остаются на месте.

**Никогда не удаляйте папку и не распаковывайте новую версию на чистое
место** — так теряются база и фотографии. Обновлять нужно поверх.

| Останется | Заменится |
|---|---|
| `db.sqlite3` — вся база | код приложения |
| `media\` — фотографии товаров | `templates\` — интерфейс |
| `.env` — ваши настройки | `requirements.txt` |
| `venv\` — окружение | миграции |

### Одной командой

Закройте окно с работающим сервером, затем в папке `deploy\windows`
щёлкните по **`ОБНОВИТЬ.bat`**.

Скрипт делает всё по порядку: снимает копию базы, фотографий и настроек,
получает новый код, обновляет библиотеки, применяет миграции, собирает
оформление и проверяет результат.

**Если проверка не прошла, база автоматически возвращается из копии,**
снятой в начале. Код при этом остаётся обновлённым — скрипт не откатывает
его сам, чтобы не потерять ваши правки, если они были, но подсказывает,
как это сделать.

Полезные ключи:

```powershell
.\update.ps1 -SkipMedia   # не копировать фотографии (когда их много)
.\update.ps1 -NoPull      # код уже распакован вручную, только доделать
```

Второй ключ нужен, если обновляетесь не через git, а распаковкой архива:
сначала распакуйте файлы поверх папки, потом запустите скрипт с `-NoPull`.

### Вручную

Если хочется контролировать каждый шаг:

```powershell
cd D:\warehouse_project
.\venv\Scripts\Activate.ps1

# 1. Остановить систему — Ctrl+C в окне сервера

# 2. Копия. Именно до всего остального
mkdir backups\ручная -Force
python -c "import sqlite3; c=sqlite3.connect('db.sqlite3'); c.execute('VACUUM INTO ?', ('backups/ручная/db.sqlite3',)); c.close()"
Copy-Item media backups\ручная\media -Recurse -Force
Copy-Item .env  backups\ручная\.env

# 3. Код
git pull

# 4. Библиотеки
pip install -r requirements.txt --upgrade

# 5. База
python manage.py migrate

# 6. Оформление
python manage.py collectstatic --noinput

# 7. Проверка
python manage.py checksetup
```

Копию снимайте **при остановленной системе**. SQLite держит часть свежих
записей в служебном журнале рядом с базой, поэтому копия «на ходу» может
оказаться без последних проведённых документов — молча, без ошибки.
Команда с `VACUUM INTO` выше обходит это: она снимает согласованную копию
средствами самой SQLite.

### После обновления

`checksetup` не формальность: он ловит именно то, что ломается при
обновлении — неприменённые миграции, несобранную статику, несовместимые
версии библиотек.

Но он не увидит, если поехала вёрстка. Откройте руками две-три рабочие
страницы: приход, отчёты, витрину.

### Чего не делать

**Не обновляйте библиотеки поодиночке.** Команда вида
`pip install django-filter --upgrade` без ограничений подтягивает
несовместимые версии — именно так система однажды перестала запускаться.
Только `pip install -r requirements.txt --upgrade`: в этом файле границы
версий, проверенные на тестах.

**Не пропускайте `migrate`.** Симптом — страница падает с ошибкой про
несуществующий столбец. Выглядит как поломка кода, а на деле база просто
отстала от него.

### Если что-то сломалось

Обратных миграций в проекте нет, поэтому откат — это восстановление
копии, а не «отменить migrate»:

```powershell
# остановить систему, затем
Copy-Item backups\2026-08-06_12-30\db.sqlite3 db.sqlite3 -Force
git log --oneline -5                 # найти предыдущую версию
git checkout <номер-коммита>
pip install -r requirements.txt --upgrade
python manage.py checksetup
```

Копии лежат в папке `backups`, каждая в своей подпапке с датой и временем.
Удалять их можно, когда убедитесь, что обновление прижилось.

---

## Частые проблемы на Windows

### «python: command not found» / «не является внутренней командой»

Python не в PATH. Варианты:
1. Переустановите Python, отметив «Add python.exe to PATH».
2. Или используйте лаунчер: `py -3 manage.py runserver` вместо `python manage.py runserver`.
3. Или укажите полный путь: `C:\Users\ИмяПользователя\AppData\Local\Programs\Python\Python311\python.exe`

### Ошибка выполнения `.ps1` скриптов

```
File ...\Activate.ps1 cannot be loaded because running scripts is
disabled on this system.
```

Решение (один раз для текущего пользователя, без прав администратора):

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### `pip install` зависает или выдаёт ошибку сборки (building wheel)

Обычно на Windows все пакеты проекта ставятся из готовых `.whl`-файлов
без компиляции. Если всё же ошибка — обновите pip и setuptools:

```powershell
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

### Порт 8000 занят

```powershell
# Узнать, какой процесс занял порт
netstat -ano | findstr :8000

# Завершить процесс по PID (последнее число в строке выше)
taskkill /PID <номер> /F

# Или просто запустить на другом порту
python manage.py runserver 8001
```

### Путь к проекту слишком длинный (MAX_PATH)

Редко, но возможно при глубокой вложенности папок — Windows по умолчанию
ограничивает пути 260 символами. Распаковывайте архив ближе к корню диска,
например в `C:\projects\warehouse_project`, а не в глубоко вложенные
папки типа `C:\Users\Имя\Documents\Работа\Проекты\2026\Складской учёт\...`.

### Кириллица отображается «кракозябрами» в консоли

Если PowerShell показывает вместо русских букв нечитаемые символы:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001
```

Выполните обе команды перед запуском `python manage.py runserver`.

### Антивирус блокирует venv или медленно устанавливает зависимости

Некоторые антивирусы (включая Windows Defender) сканируют каждый файл
виртуального окружения при создании, что может занять 1-2 минуты — это
нормально, просто подождите. Если совсем не отвечает — добавьте папку
проекта в исключения антивируса.

---

## Сравнение команд: Windows (PowerShell) vs Linux/macOS

| Действие | Windows (PowerShell) | Linux / macOS |
|----------|----------------------|----------------|
| Создать venv | `python -m venv venv` | `python3 -m venv venv` |
| Активировать venv | `.\venv\Scripts\Activate.ps1` | `source venv/bin/activate` |
| Деактивировать | `deactivate` | `deactivate` |
| Распаковать архив | `tar -xzf file.tar.gz` или «Извлечь всё» для `.zip` | `tar -xzf file.tar.gz` |
| Переменная окружения (разово) | `$env:DEBUG="True"` | `export DEBUG=True` |
| Путь в .env | `C:\projects\...` (Django понимает оба варианта) | `/home/user/...` |
| curl | `curl.exe ...` (не просто `curl`) | `curl ...` |

---

## Итоговый чек-лист

```powershell
# 1. Распаковать
tar -xzf warehouse_project.tar.gz
cd warehouse_project

# 2. Виртуальное окружение
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Зависимости
pip install -r requirements.txt

# 4. База данных
python manage.py migrate

# 5. Администратор
python manage.py createsuperuser

# 6. Запуск
python manage.py runserver

# 7. Открыть http://127.0.0.1:8000
```

Если что-то не работает — смотрите раздел «Частые проблемы на Windows»
выше или CHECKLIST.md.
