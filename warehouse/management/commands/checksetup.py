"""
Проверка установки: `python manage.py checksetup`.

Встроенная команда `check` проверяет, что настройки Django не противоречат
друг другу. Но она ничего не скажет о том, что забыли применить миграции,
не завели ни одного склада или оставили в .env отладочный режим на рабочем
сервере — а именно из-за таких мелочей установка «вроде прошла», а система
не работает.

Эта команда проходит по всему, что обычно забывают, и говорит не кодом
ошибки, а понятной фразой: что не так и что с этим делать.

Возвращает ненулевой код, если найдено хоть что-то критичное, — так её
можно поставить в сценарий развёртывания.
"""
import os
import sys
from pathlib import Path

import django
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = 'Проверить, что система установлена и настроена правильно'

    def add_arguments(self, parser):
        parser.add_argument(
            '--production', action='store_true',
            help='Строгие требования для рабочего сервера: отладочный режим '
                 'и ключ по умолчанию считаются ошибками, а не замечаниями')

    def handle(self, *args, **options):
        self.production = options['production']
        self.problems = []    # мешает работать
        self.warnings = []    # стоит поправить
        self.notes = []       # к сведению

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(
            '  Проверка установки системы складского учёта'))
        self.stdout.write('  ' + '─' * 58)

        self._check_python()
        self._check_packages()
        self._check_secret_key()
        self._check_debug()
        self._check_allowed_hosts()
        self._check_database()
        self._check_migrations()
        self._check_directories()
        self._check_static()
        self._check_users()
        self._check_reference_data()
        self._check_email()
        self._check_backups()

        return self._report()

    # --- Отдельные проверки -------------------------------------------------
    def _check_python(self):
        major, minor = sys.version_info[:2]
        version = f'{major}.{minor}.{sys.version_info[2]}'
        if (major, minor) < (3, 10):
            self.fail(f'Python {version} слишком старый',
                      'Нужен Python 3.10 или новее. Установите с '
                      'python.org и создайте окружение заново.')
        else:
            self.ok(f'Python {version}')

    def _check_packages(self):
        try:
            import rest_framework
        except ImportError:
            self.fail('Не установлены зависимости',
                      'Выполните: pip install -r requirements.txt')
            return

        self.ok(f'Django {django.get_version()}, '
                f'DRF {rest_framework.VERSION}')

        # Django 6.1 несовместима с нынешним DRF: из django.utils.cache
        # убрали cc_delim_re, который тот импортирует
        if django.VERSION[:2] >= (6, 1):
            self.fail(
                f'Django {django.get_version()} несовместима с DRF',
                'Установите Django ниже 6.1: '
                'pip install -r requirements.txt --upgrade')

    def _check_secret_key(self):
        key = settings.SECRET_KEY
        insecure = (not key
                    or key.startswith('django-insecure')
                    or key.startswith('ЗАМЕНИТЕ')
                    or len(key) < 40)
        if not insecure:
            self.ok('Секретный ключ задан')
            return

        message = ('Сгенерируйте свой: python -c "from '
                   'django.core.management.utils import '
                   'get_random_secret_key as k; print(k())" '
                   'и впишите в .env строкой SECRET_KEY=...')
        if self.production or not settings.DEBUG:
            self.fail('Используется небезопасный SECRET_KEY', message)
        else:
            self.warn('Используется отладочный SECRET_KEY', message)

    def _check_debug(self):
        if not settings.DEBUG:
            self.ok('Отладочный режим выключен')
            return
        message = ('При DEBUG=True страница ошибки показывает исходный код, '
                   'секретный ключ и пароль от базы — и видит её любой, кто '
                   'открыл систему. Для рабочего сервера поставьте в .env '
                   'DEBUG=False.')
        if self.production:
            self.fail('Включён отладочный режим', message)
        else:
            self.warn('Включён отладочный режим (это нормально для '
                      'разработки)', message)

    def _check_allowed_hosts(self):
        hosts = settings.ALLOWED_HOSTS
        if settings.DEBUG:
            self.note('ALLOWED_HOSTS не действует при DEBUG=True: '
                      'принимаются любые адреса')
            return
        if not hosts or hosts == ['']:
            self.fail('Не задан ALLOWED_HOSTS',
                      'Впишите в .env адреса, по которым открывают систему, '
                      'например: ALLOWED_HOSTS=localhost,127.0.0.1,'
                      '192.168.1.50')
        elif '*' in hosts:
            self.warn('ALLOWED_HOSTS разрешает любой адрес',
                      'Перечислите конкретные адреса — так запросы с чужих '
                      'имён не дойдут до приложения.')
        else:
            self.ok(f'Адреса доступа: {", ".join(hosts)}')

    def _check_database(self):
        engine = settings.DATABASES['default']['ENGINE'].rsplit('.', 1)[-1]
        try:
            connection.ensure_connection()
        except Exception as error:
            self.fail(f'Нет связи с базой данных ({engine})',
                      f'{error}\nПроверьте параметры DB_* в .env. Для '
                      f'PostgreSQL убедитесь, что сервер запущен.')
            return

        self.ok(f'База данных доступна ({engine})')

        if engine == 'sqlite3':
            path = Path(settings.DATABASES['default']['NAME'])
            if path.exists():
                size = path.stat().st_size / 1024 / 1024
                self.note(f'SQLite, файл {path.name}, {size:.1f} МБ. '
                          f'Копию снимайте командой backup, а не копированием '
                          f'файла: часть свежих записей лежит в служебном '
                          f'журнале рядом с базой и в копию не попадёт.')

    def _check_migrations(self):
        try:
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(
                executor.loader.graph.leaf_nodes())
        except Exception as error:
            self.fail('Не удалось проверить миграции', str(error))
            return

        if plan:
            names = ', '.join(f'{m.app_label}.{m.name}' for m, _ in plan[:3])
            more = f' и ещё {len(plan) - 3}' if len(plan) > 3 else ''
            self.fail(f'Не применены миграции: {names}{more}',
                      'Выполните: python manage.py migrate')
        else:
            self.ok('Миграции применены')

    def _check_directories(self):
        for name, path in (('media (фото товаров)', settings.MEDIA_ROOT),
                           ('logs (журналы)', settings.BASE_DIR / 'logs')):
            path = Path(path)
            if not path.exists():
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    self.note(f'Создан каталог {name}')
                except OSError as error:
                    self.fail(f'Не удаётся создать каталог {name}',
                              str(error))
                    continue
            if not os.access(path, os.W_OK):
                self.fail(f'Нет прав на запись в {name}',
                          f'Дайте права на каталог {path}')
            else:
                self.ok(f'Каталог {name} доступен на запись')

    def _check_static(self):
        static_root = Path(settings.STATIC_ROOT)
        if settings.DEBUG:
            self.note('Статику раздаёт сам Django (DEBUG=True), '
                      'collectstatic не требуется')
            return

        if not static_root.exists() or not any(static_root.iterdir()):
            self.fail('Статика не собрана',
                      'Выполните: python manage.py collectstatic --noinput\n'
                      'Без этого интерфейс останется без стилей.')
        else:
            count = sum(1 for _ in static_root.rglob('*') if _.is_file())
            self.ok(f'Статика собрана ({count} файлов)')

    def _check_users(self):
        User = get_user_model()
        try:
            total = User.objects.count()
            admins = User.objects.filter(is_superuser=True).count()
        except Exception:
            return   # база недоступна, о чём уже сказано выше

        if total == 0:
            self.fail('Не заведено ни одного сотрудника',
                      'Создайте администратора: '
                      'python manage.py createsuperuser')
        elif admins == 0:
            self.fail('Нет ни одного администратора',
                      'Создайте: python manage.py createsuperuser')
        else:
            self.ok(f'Сотрудников: {total}, из них администраторов: {admins}')

    def _check_reference_data(self):
        from warehouse.models import Material, Product, Warehouse

        try:
            warehouses = Warehouse.objects.count()
            items = Material.objects.count() + Product.objects.count()
        except Exception:
            return

        if warehouses == 0:
            self.warn('Не заведено ни одного склада',
                      'Без склада нельзя оприходовать товар. Заведите в '
                      'админке: Склады → Добавить.')
        else:
            self.ok(f'Складов: {warehouses}, позиций в справочниках: {items}')

    def _check_email(self):
        # Сравниваем по модулю, а не по имени класса: у консольного и у
        # SMTP-варианта класс называется одинаково — EmailBackend.
        backend = settings.EMAIL_BACKEND
        if '.console.' in backend or '.locmem.' in backend or '.dummy.' in backend:
            message = ('Письма выводятся в консоль, а не отправляются. '
                       'Для рабочего сервера настройте SMTP — см. '
                       'SMTP_SETUP.md')
            if self.production:
                self.warn('Почта не настроена', message)
            else:
                self.note(message)
        elif not settings.EMAIL_HOST:
            self.warn('Выбран SMTP, но не задан адрес сервера',
                      'Заполните EMAIL_HOST в .env — см. SMTP_SETUP.md')
        else:
            self.ok(f'Почта: {settings.EMAIL_HOST}')

    def _check_backups(self):
        """Давно ли снимали копию.

        Проверка не о том, настроено ли расписание — узнать это надёжно
        нельзя, задание может стоять на другом компьютере или копии могут
        уноситься куда-то ещё. Смотрим на итог: есть ли свежая копия. Если
        последней больше недели, значит что-то сломалось или её не
        настраивали вовсе.
        """
        from datetime import datetime

        root = Path(settings.BASE_DIR) / 'backups'
        folders = []
        if root.exists():
            for path in root.iterdir():
                if not path.is_dir():
                    continue
                try:
                    folders.append(
                        datetime.strptime(path.name[:19], '%Y-%m-%d_%H-%M-%S'))
                except ValueError:
                    continue

        if not folders:
            self.warn(
                'Резервных копий нет',
                'Настройте ежедневное копирование: в папке deploy\\windows '
                'выполните .\\НАСТРОИТЬ-КОПИИ.ps1 -To D:\\Копии\\Склад. '
                'База лежит в одном файле — без копии отказ диска означает '
                'потерю всего учёта.')
            return

        last = max(folders)
        days = (datetime.now() - last).days
        if days > 7:
            self.warn(
                f'Последняя резервная копия {days} дн. назад '
                f'({last:%d.%m.%Y})',
                'Похоже, копирование не работает. Проверьте задание в '
                'планировщике Windows или снимите копию вручную: '
                'deploy\\windows\\backup.ps1')
        else:
            self.ok(f'Резервные копии: последняя {last:%d.%m.%Y %H:%M}, '
                    f'всего {len(folders)}')

    # --- Вывод --------------------------------------------------------------
    def ok(self, text):
        self.stdout.write('  ' + self.style.SUCCESS('✓ ') + text)

    def fail(self, text, hint):
        self.problems.append((text, hint))
        self.stdout.write('  ' + self.style.ERROR('✗ ') + text)

    def warn(self, text, hint):
        self.warnings.append((text, hint))
        self.stdout.write('  ' + self.style.WARNING('! ') + text)

    def note(self, text):
        self.notes.append(text)
        self.stdout.write('  ' + self.style.HTTP_INFO('· ') + text)

    def _report(self):
        self.stdout.write('  ' + '─' * 58)

        if self.problems:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR(
                f'  Мешает работе: {len(self.problems)}'))
            for text, hint in self.problems:
                self.stdout.write('')
                self.stdout.write(f'  ✗ {text}')
                for line in hint.split('\n'):
                    self.stdout.write(f'      {line}')

        if self.warnings:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                f'  Стоит поправить: {len(self.warnings)}'))
            for text, hint in self.warnings:
                self.stdout.write('')
                self.stdout.write(f'  ! {text}')
                for line in hint.split('\n'):
                    self.stdout.write(f'      {line}')

        self.stdout.write('')
        if self.problems:
            self.stdout.write(self.style.ERROR(
                '  Система к работе не готова.'))
            self.stdout.write('')
            sys.exit(1)

        if self.warnings:
            self.stdout.write(self.style.WARNING(
                '  Система работает, но замечания стоит устранить.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                '  Всё в порядке, система готова к работе.'))
        self.stdout.write('')
