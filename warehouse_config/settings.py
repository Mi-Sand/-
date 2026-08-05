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
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # Раздаёт собранную статику силами приложения. Нужен, когда перед
    # Django нет nginx: в боевом режиме Django статику не отдаёт, и
    # интерфейс остался бы без стилей. Идёт сразу после
    # SecurityMiddleware — так требует whitenoise.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
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
STATICFILES_DIRS = [BASE_DIR / 'static']

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

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
