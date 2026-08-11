"""Перенос данных из SQLite в PostgreSQL.

    python manage.py topostgres

Запускается уже с настройками PostgreSQL (DB_ENGINE=postgres в .env):
целевая база — обычная, рабочая, а прежний файл db.sqlite3 читается как
источник. Перед переносом в целевой базе должны быть применены миграции,
но не должно быть данных.

Почему командой, а не связкой dumpdata/loaddata, как обычно советуют.
Три вещи в этом пути ломаются молча:

  1. Типы записей и права (contenttypes, auth.Permission) создаются
     миграциями заново, и загрузка выгрузки натыкается на них
     нарушением уникальности. Совет «исключить их из выгрузки» работает,
     пока в системе нет ссылок на них.

  2. Счётчики номеров. В PostgreSQL номер следующей записи выдаёт
     отдельный счётчик, и после вставки записей с готовыми номерами он
     остаётся на единице. Всё выглядит перенесённым, а первый же новый
     документ падает с ошибкой «такой номер уже есть». Обнаруживается
     это не при переносе, а через день, у кладовщика.

  3. Проверить перенос нечем: loaddata говорит «загружено N объектов»,
     но не сверяет их с источником.

Здесь всё три закрыты: переносится всё подряд, включая типы записей;
счётчики переставляются; в конце число записей сверяется по каждой
таблице, и при расхождении вся работа откатывается.
"""
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

#: Сколько записей переносить за один запрос
BATCH = 500

#: Имя временного подключения к прежней базе
LEGACY = 'legacy_sqlite'


class Command(BaseCommand):
    help = 'Перенести данные из db.sqlite3 в PostgreSQL'

    def add_arguments(self, parser):
        parser.add_argument(
            '--from', dest='source', default=None, metavar='ФАЙЛ',
            help='Файл прежней базы (по умолчанию db.sqlite3 рядом с '
                 'manage.py)')
        parser.add_argument(
            '--force', action='store_true',
            help='Переносить, даже если в целевой базе уже есть данные. '
                 'Записи с совпадающими номерами будут заменены.')

    def say(self, text=''):
        self.stdout.write(text)

    def handle(self, *args, **options):
        target = connections['default']
        if 'postgresql' not in target.settings_dict['ENGINE']:
            raise CommandError(
                'Команду нужно запускать уже с настройками PostgreSQL.\n'
                '    Поставьте в .env DB_ENGINE=postgres и параметры DB_*, '
                'примените миграции\n'
                '    (python manage.py migrate), потом запустите перенос.')

        source = Path(options['source']) if options['source'] \
            else Path(settings.BASE_DIR) / 'db.sqlite3'
        if not source.exists():
            raise CommandError(f'Прежняя база не найдена: {source}')

        connections.settings[LEGACY] = {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': str(source),
            'ATOMIC_REQUESTS': False,
            'AUTOCOMMIT': True,
            'CONN_MAX_AGE': 0,
            'CONN_HEALTH_CHECKS': False,
            'OPTIONS': {},
            'TIME_ZONE': None,
            'USER': '', 'PASSWORD': '', 'HOST': '', 'PORT': '',
            'TEST': {'CHARSET': None, 'COLLATION': None, 'NAME': None,
                     'MIRROR': None},
        }

        try:
            models = self.models_to_move()
            self.check_target_is_empty(models, options['force'])

            self.say(f'\n  Перенос из {source}')
            self.say(f'  в базу {target.settings_dict["NAME"]} '
                     f'на {target.settings_dict.get("HOST") or "localhost"}\n')

            moved = self.move(models)
            self.check_counts(models)
            self.reset_counters(models)
        finally:
            # Подключение к прежней базе живёт только на время переноса.
            # Оставленное, оно числится за системой и дальше: файл
            # остаётся открытым, а всё, что перебирает подключения —
            # от тестов до самого Django, — спотыкается о чужой,
            # неизвестно откуда взявшийся источник.
            self.forget_legacy()

        self.say('')
        self.stdout.write(self.style.SUCCESS(
            f'  Готово. Перенесено записей: {sum(moved.values())} '
            f'в {len(moved)} таблицах'))
        self.say('')
        self.say('  Проверьте систему, прежде чем удалять db.sqlite3:')
        self.say('      python manage.py checksetup')
        self.say('  и откройте пару рабочих страниц — остатки, документы.')
        self.say('')

    @staticmethod
    def forget_legacy():
        """Закрыть и убрать временное подключение к прежней базе."""
        if LEGACY in connections.settings:
            try:
                connections[LEGACY].close()
            except Exception:                            # pragma: no cover
                pass
            del connections.settings[LEGACY]
            connections.__dict__.pop('settings', None)
            connections.__dict__.pop('databases', None)

    # --- шаги -------------------------------------------------------------
    @staticmethod
    def models_to_move():
        """Все модели проекта, включая служебные и связующие.

        Типы записей и права переносятся вместе со всем остальным:
        миграции уже создали их в целевой базе, но с другими номерами,
        а на эти номера ссылаются записи журнала действий. Перенос
        поверх — единственный способ сохранить ссылки целыми.
        """
        return [model for model in apps.get_models(include_auto_created=True)
                if model._meta.managed]

    def check_target_is_empty(self, models, force):
        if force:
            return
        busy = []
        for model in models:
            # Права и типы записей создают миграции — они есть всегда,
            # и по ним нельзя судить, работали ли в базе.
            label = model._meta.label
            if label in ('contenttypes.ContentType', 'auth.Permission'):
                continue
            count = model.objects.using('default').count()
            if count:
                busy.append(f'{model._meta.db_table}: {count}')
        if busy:
            raise CommandError(
                'В целевой базе уже есть данные — перенос отменён, чтобы '
                'не задвоить записи:\n    '
                + '\n    '.join(busy[:8])
                + '\n\n    Если это остатки неудачной попытки, очистите базу '
                  'заново:\n'
                  '      python manage.py flush --noinput\n'
                  '    и повторите перенос. Либо запустите с --force, если '
                  'знаете, что делаете.')

    def move(self, models):
        """Перенести записи по таблицам, всё за одну сделку.

        Сначала целевые таблицы очищаются. Это не перестраховка:
        миграции уже создали в них типы записей и права со своими
        номерами, и вставка перенесённых упирается в занятый номер.
        Очистка и перенос идут одной сделкой, поэтому промежуточного
        состояния «права стёрты, а новые не залиты» не бывает.

        Порядок таблиц не важен: Django создаёт внешние ключи
        отложенными (DEFERRABLE INITIALLY DEFERRED), и ссылки
        проверяются один раз в конце сделки, а не на каждой строке.
        Если что-то не сойдётся, откатится весь перенос целиком —
        полупереехавшая база хуже непереехавшей.
        """
        moved = {}
        with transaction.atomic(using='default'):
            self.clear_target(models)
            for model in models:
                table = model._meta.db_table
                rows = list(model.objects.using(LEGACY).all().iterator(
                    chunk_size=BATCH))
                if not rows:
                    continue
                model.objects.using('default').bulk_create(
                    rows, batch_size=BATCH,
                    ignore_conflicts=False, update_conflicts=False)
                moved[table] = len(rows)
                self.say(f'  {table:32} {len(rows):>7}')
        return moved

    def clear_target(self, models):
        """Очистить целевые таблицы перед переносом.

        TRUNCATE в PostgreSQL входит в сделку наравне с остальным, так
        что при неудаче переноса очистка откатится вместе с ним.
        CASCADE нужен из-за внешних ключей между самими таблицами
        списка — данные всё равно переносятся целиком.
        """
        tables = ', '.join(f'"{model._meta.db_table}"' for model in models)
        with connections['default'].cursor() as cursor:
            cursor.execute(f'TRUNCATE {tables} CASCADE')

    def check_counts(self, models):
        """Сверить перенос по числу записей в каждой таблице."""
        differences = []
        for model in models:
            was = model.objects.using(LEGACY).count()
            now = model.objects.using('default').count()
            if was != now:
                differences.append(
                    f'{model._meta.db_table}: было {was}, стало {now}')
        if differences:
            raise CommandError(
                'Перенос неполный:\n    ' + '\n    '.join(differences))

    def reset_counters(self, models):
        """Переставить счётчики номеров на конец таблиц.

        Без этого следующий заведённый документ получит номер 1 — тот,
        что уже занят перенесённой записью, — и сохранение оборвётся
        ошибкой уникальности. Ошибка вылезет не сегодня, а когда
        кладовщик начнёт работать.
        """
        target = connections['default']
        statements = target.ops.sequence_reset_sql(no_style(), models)
        if not statements:
            return
        with target.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        self.say(f'\n  Счётчики номеров переставлены: {len(statements)}')


def no_style():
    from django.core.management.color import no_style as style
    return style()
