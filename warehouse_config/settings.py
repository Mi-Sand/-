"""
Настройки проекта системы складского учёта ООО «ЛЕКО».

По умолчанию используется SQLite, чтобы проект запускался сразу, без
установки СУБД. Для боевой эксплуатации, как описано в отчёте, задайте
в файле .env переменную DB_ENGINE=postgres и параметры PostgreSQL.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Загрузка переменных окружения из файла .env (если он есть)
load_dotenv(BASE_DIR / '.env')

# Отладочный режим по умолчанию выключен.
#
# Это важнее, чем кажется: при DEBUG=True страница любой ошибки показывает
# исходный код, значение SECRET_KEY и параметры подключения к базе — и
# видит её каждый, кто открыл систему в браузере. Если переменную забыли
# задать на рабочем сервере, безопаснее остаться без подробностей, чем
# раскрыть их наружу. Для разработки DEBUG=True ставится в файле .env.
DEBUG = os.environ.get('DEBUG', 'False').lower() in ('1', 'true', 'yes')

# Ключ, которым подписываются сессии и токены.
#
# Запасное значение существует только для разработки. В боевом режиме его
# нет намеренно: система откажется запускаться, пока ключ не задан, и это
# лучше, чем молча работать с общеизвестным ключом — по нему подделывается
# сессия любого сотрудника, включая администратора.
SECRET_KEY = os.environ.get('SECRET_KEY', '')
if not SECRET_KEY or SECRET_KEY.startswith('ЗАМЕНИТЕ'):
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-key-only-for-local-development'
    else:
        raise RuntimeError(
            'Не задана переменная SECRET_KEY.\n\n'
            'В боевом режиме (DEBUG=False) ключ обязателен: на нём '
            'построены подписи сессий, и с общеизвестным ключом любой '
            'может выдать себя за администратора.\n\n'
            'Сгенерировать новый:\n'
            '    python -c "from django.core.management.utils import '
            'get_random_secret_key as k; print(k())"\n\n'
            'и вписать результат в .env строкой SECRET_KEY=...')

ALLOWED_HOSTS = ['*'] if DEBUG else os.environ.get(
    'ALLOWED_HOSTS', 'localhost,127.0.0.1'
).split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Сторонние библиотеки
    'rest_framework',
    'corsheaders',
    'django_filters',

    # Приложения проекта
    'accounts',
    'warehouse',
    'inventory',
    'reports',
    'billing',
    'audit',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    # Обычный посредник безопасности Django, но перенаправление на https
    # временное, а не «навсегда»: иначе одна ошибка в настройках
    # закрывает вход насовсем силами самого браузера. Подробности —
    # в warehouse/https.py.
    'warehouse.https.RecoverableSecurityMiddleware',
    # Раздаёт собранную статику силами приложения. Нужен, когда перед
    # Django нет nginx: в боевом режиме Django статику не отдаёт, и
    # интерфейс остался бы без стилей. Идёт сразу после
    # SecurityMiddleware — так требует whitenoise.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # Запоминает вошедшего сотрудника: журналу действий он нужен в
    # сигналах, куда запрос не передаётся. Идёт после проверки входа —
    # раньше пользователя ещё нет.
    'audit.current_user.CurrentUserMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'warehouse_config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                # Права текущего сотрудника — чтобы страницы не показывали
                # кнопок, которые его роли всё равно недоступны
                'accounts.context_processors.user_permissions',
                # Адрес, телефон и реквизиты предприятия для публичных
                # страниц магазина. Правятся в одном месте — warehouse/
                # shop_info.py — и меняются сразу везде.
                'warehouse.shop_info.company_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'warehouse_config.wsgi.application'

# --- База данных -----------------------------------------------------------
# Как в отчёте: PostgreSQL для боевой эксплуатации. Для мгновенного запуска
# и тестов используется SQLite (переключается переменной DB_ENGINE).
if os.environ.get('DB_ENGINE', 'sqlite') == 'postgres':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'warehouse_db'),
            'USER': os.environ.get('DB_USER', 'warehouse_user'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            # Сколько ждать, если база занята другим. Без ожидания
            # запрос отвечал бы отказом «database is locked» сразу.
            'OPTIONS': {
                'timeout': 20,
                # journal_mode=WAL — запись ведётся в отдельный журнал,
                # а не поверх самой базы. Из-за этого читающий и
                # пишущий перестают мешать друг другу: пока экономист
                # строит отчёт, кладовщик проводит документы.
                #
                # Разница измеримая. На проверке (три отчёта подряд и
                # проведение документов, четыре секунды): без WAL —
                # 6257 проведённых документов, а отчёты то и дело
                # отвечали «база занята»; с WAL — 15596 документов и
                # ни одного отказа.
                #
                # synchronous=NORMAL — при WAL это обычная настройка:
                # база не ждёт подтверждения диска на каждой записи.
                # Потерять можно только последние секунды работы и
                # только при отключении питания; сама база остаётся
                # целой.
                #
                # Важно: WAL не работает на сетевой папке. Если файл
                # базы окажется на общем диске, режим нужно убрать —
                # или переходить на PostgreSQL, для которого сеть и
                # предназначена.
                'init_command': (
                    'PRAGMA journal_mode=WAL;'
                    'PRAGMA synchronous=NORMAL;'
                ),
                # Сделка сразу берёт право на запись, а не повышает его
                # посреди работы. Иначе два одновременных проведения
                # могли столкнуться на полпути, и одно откатывалось бы
                # с ошибкой вместо того, чтобы подождать.
                'transaction_mode': 'IMMEDIATE',
            },
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.'
             'UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.'
             'MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.'
             'CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.'
             'NumericPasswordValidator'},
]

# Собственная модель пользователя с ролями (кладовщик, экономист и т. д.)
AUTH_USER_MODEL = 'accounts.User'

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

# Своей статики у системы нет: оформление страниц лежит прямо в разметке,
# а значки — в templates/_icons.html. Папка static нужна только тому, кто
# захочет добавить свои файлы, и на новой установке её не бывает: git не
# переносит пустые папки.
#
# Раньше папка перечислялась безусловно, и каждая команда — обновление,
# снятие копии, проверка установки — начиналась с предупреждения
# staticfiles.W004 о несуществующей папке. Предупреждение безобидное, но
# на экране обновления выглядит поломкой, и настоящую беду за ним видно
# хуже.
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').is_dir() else []

# Куда collectstatic складывает статику для боевого режима.
#
# Без этой настройки команда завершается ошибкой ImproperlyConfigured. В
# docker-compose она выполняется при старте контейнера, поэтому раньше
# контейнер просто не поднимался — а причина из журнала была неочевидна.
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Хранилище со сжатием: whitenoise заранее готовит сжатые копии файлов.
# Вариант с хешами в именах сознательно не берём — он падает, если в
# разметке упомянут файл, которого нет, а для этой системы такая строгость
# только мешает.
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'

# Медиафайлы (загруженные пользователями: фото и видео товаров)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Раздавать ли фотографии товаров силами самого Django.
#
# Нужно, когда перед приложением нет веб-сервера: при запуске на офисном
# компьютере или через `runserver` в боевом режиме. Без этого витрина
# осталась бы без картинок, причём молча — файлы просто не находятся.
# В docker-compose переменная выключена: там их отдаёт nginx, и делает
# это быстрее.
SERVE_MEDIA_FILES = os.environ.get(
    'SERVE_MEDIA_FILES', 'True').lower() in ('1', 'true', 'yes')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Django REST Framework --------------------------------------------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    # LimitOffsetPagination поддерживает параметр ?limit= — это позволяет
    # клиенту запрашивать нужный объём данных за раз (функция apiCallAll
    # во фронтенде забирает все страницы подряд по ссылке next).
    'DEFAULT_PAGINATION_CLASS':
        'warehouse.pagination.WarehouseLimitOffsetPagination',
    'PAGE_SIZE': 25,
}

# --- CORS (взаимодействие с клиентской частью) ------------------------------
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOW_CREDENTIALS = True

# --- Кэширование справочников (фрагмент 18 отчёта) ---------------------------
# --- Работа по HTTPS --------------------------------------------------------
#
# Пока система живёт внутри предприятия и наружу не выставлена, шифрование
# не обязательно: чтобы прочитать пароль кладовщика, нужно уже быть в той
# же сети со своим ноутбуком. Но как только систему открывают наружу —
# или в сети появляются чужие устройства, — открытый текст перестаёт быть
# приемлемым.
#
# Включается одной строкой в .env: USE_HTTPS=True. Порядок целиком, вместе
# с сертификатом и nginx, описан в HTTPS.md.
#
# ВАЖНО: включать только когда HTTPS действительно работает. Иначе
# перенаправление уведёт браузер на адрес, которого нет, а куки с пометкой
# «только по защищённому соединению» просто не дойдут, и вход перестанет
# работать. Проверка `checksetup` об этом предупреждает.
USE_HTTPS = os.environ.get('USE_HTTPS', 'False').lower() in (
    'true', '1', 'yes')

if USE_HTTPS:
    # Приложение стоит за nginx, и сам запрос до него доходит уже по
    # обычному HTTP. О том, что снаружи было шифрование, говорит
    # заголовок от nginx — без этой строки Django считал бы соединение
    # незащищённым и перенаправлял бы браузер по кругу.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

    # Обращение по http перенаправляется на https
    SECURE_SSL_REDIRECT = True

    # Куки сессии и защиты от подделки запросов передаются только по
    # защищённому соединению. Это и есть главное: без пометки они уйдут
    # и по открытому каналу, где их можно прочитать.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # Полгода помнить, что на этот адрес ходят только по https. Меньше
    # ставить бессмысленно, больше — рискованно: снять эту память до
    # срока нельзя, и ошибка с сертификатом закроет доступ надолго.
    #
    # Срок вынесен в .env именно поэтому. Пока сертификат обкатывается,
    # разумно поставить HSTS_SECONDS=0 (памяти нет, вернуться можно в
    # любой момент) или на день; когда всё устоялось — вернуть полгода.
    # Значение по умолчанию не изменилось: кто не трогал .env, получит
    # прежние полгода.
    try:
        SECURE_HSTS_SECONDS = int(os.environ.get('HSTS_SECONDS',
                                                 60 * 60 * 24 * 180))
    except ValueError:
        raise RuntimeError(
            'Переменная HSTS_SECONDS должна быть числом секунд '
            '(например, 0 на время настройки или 15552000 — полгода).')
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    # preload не включаем: это список внутри самих браузеров, из
    # которого адрес предприятия убирать пришлось бы месяцами.
    SECURE_HSTS_PRELOAD = False

    # Адреса, с которых принимаются формы. Без них вход по https
    # отвечает «подделка запроса»: Django сверяет источник, а он теперь
    # начинается с https.
    CSRF_TRUSTED_ORIGINS = [
        origin.strip()
        for origin in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',')
        if origin.strip()
    ]

# Эти две настройки не зависят от шифрования и ничего не ломают по http,
# поэтому включены всегда.
#
# Первая запрещает браузеру угадывать тип файла по содержимому: без неё
# загруженное фото товара, внутри которого лежит разметка, может быть
# показано как страница.
SECURE_CONTENT_TYPE_NOSNIFF = True
# Вторая не даёт чужому сайту показать систему у себя во врезке и
# ловить нажатия сотрудника.
X_FRAME_OPTIONS = 'DENY'

# --- Корзина удалённых документов -------------------------------------------
#
# Сколько дней держать удалённый документ, прежде чем вычистить его
# насовсем командой purgetrash. Месяц — достаточный срок: за него
# становится ясно, что документ удалили не по ошибке.
TRASH_KEEP_DAYS = int(os.environ.get('TRASH_KEEP_DAYS', '30'))

# --- Витрина: пределы для заказов без входа ---------------------------------
#
# Витрина открыта всем, и товар при оформлении заказа уходит в резерв.
# Без пределов один человек за минуту оформлял сотню заказов, весь товар
# оказывался обещанным, а настоящие покупатели видели «нет в наличии».
#
# Значения щедрые: обычный покупатель оформляет за час один заказ, редко
# два. Если жизнь покажет другое — правятся здесь.
SHOP_ORDER_LIMITS = {
    # Сколько заказов принимаем с одного адреса за час
    'per_ip_per_hour': 5,
    # И сколько — с одного номера телефона
    'per_phone_per_hour': 3,
    # Через сколько часов снимать резерв с неподтверждённого заказа.
    # Снимает команда expireorders, запускаемая планировщиком.
    'unconfirmed_hours': 24,
}

# Доверять ли заголовку X-Forwarded-For при определении адреса
# покупателя. Включать только когда перед системой стоит nginx: без
# него заголовок подделывается кем угодно, и предел частоты обходится
# одной строкой в запросе.
TRUST_FORWARDED_FOR = os.environ.get(
    'TRUST_FORWARDED_FOR', 'False').lower() in ('true', '1', 'yes')

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'warehouse-cache',
    }
}

# --- Почта для уведомлений --------------------------------------------------
# В разработке письма выводятся в консоль (значение по умолчанию).
# Для боевого режима задайте переменные EMAIL_* в .env — см. SMTP_SETUP.md.
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() in (
    '1', 'true', 'yes')
EMAIL_USE_SSL = os.environ.get('EMAIL_USE_SSL', 'False').lower() in (
    '1', 'true', 'yes')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get(
    'DEFAULT_FROM_EMAIL', 'warehouse@leko.example')
WAREHOUSE_MANAGER_EMAIL = os.environ.get(
    'WAREHOUSE_MANAGER_EMAIL', 'manager@leko.example')

# Писать ли ответственному на каждое падение остатка ниже минимума.
#
# По умолчанию нет: остаток пересчитывается при каждом проведении
# документа, и при десятке материалов набегает десяток писем в день.
# Ящик с такой рассылкой перестают читать, и предупреждения перестают
# работать вовсе. Низкие остатки перечислены в ежедневной сводке
# (manage.py dailysummary) первым же разделом.
NOTIFY_LOW_STOCK_INSTANTLY = os.environ.get(
    'NOTIFY_LOW_STOCK_INSTANTLY', 'False').lower() in ('true', '1', 'yes')

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
