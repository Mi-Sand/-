"""Резервная копия базы, фотографий и настроек.

Запускается вручную или заданием в планировщике Windows:

    python manage.py backup
    python manage.py backup --keep 30 --quiet

Копия базы снимается средством самой SQLite (VACUUM INTO), а не
копированием файла. Обычная копия «на ходу» может оказаться без
последних проведённых документов: часть свежих записей лежит в
служебном журнале рядом с базой, и в файл они ещё не переписаны.
Ошибки при этом не будет — потеря обнаружится только при восстановлении.

Снятая копия сразу проверяется на читаемость и сверяется с исходной по
числу записей. Копия, которую нельзя восстановить, хуже её отсутствия:
на неё рассчитывают.
"""
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

#: Сколько дней хранить копии, если не сказано иное
DEFAULT_KEEP_DAYS = 30

#: Формат имени папки с копией. Секунды нужны: две копии за одну минуту
#: иначе легли бы в одну папку, а VACUUM INTO не пишет поверх готового
#: файла и отказался бы.
STAMP_FORMAT = '%Y-%m-%d_%H-%M-%S'


class Command(BaseCommand):
    help = 'Резервная копия базы, фотографий и настроек'

    def add_arguments(self, parser):
        parser.add_argument(
            '--to', default=None, metavar='ПАПКА',
            help='Куда складывать копии (по умолчанию backups рядом с базой). '
                 'Стоит указать другой диск или сетевую папку: копия рядом '
                 'с базой не спасёт от отказа диска.')
        parser.add_argument(
            '--keep', type=int, default=DEFAULT_KEEP_DAYS, metavar='ДНЕЙ',
            help=f'Сколько дней хранить старые копии '
                 f'(по умолчанию {DEFAULT_KEEP_DAYS}). 0 — не удалять.')
        parser.add_argument(
            '--no-media', action='store_true',
            help='Не копировать фотографии товаров. Их много, а меняются '
                 'они редко — имеет смысл при частых копиях.')
        parser.add_argument(
            '--quiet', action='store_true',
            help='Выводить только ошибки. Для запуска по расписанию.')

    # --- вывод ----------------------------------------------------------
    def say(self, text):
        if not self.quiet:
            self.stdout.write(text)

    def ok(self, text):
        self.say(self.style.SUCCESS(f'  {text}'))

    def note(self, text):
        self.say(f'  {text}')

    # --- основное -------------------------------------------------------
    def handle(self, *args, **options):
        self.quiet = options['quiet']
        started = datetime.now()

        database = settings.DATABASES['default']
        if 'sqlite' not in database['ENGINE']:
            raise CommandError(
                'Эта команда снимает копию только с SQLite. Для PostgreSQL '
                'пользуйтесь pg_dump — он умеет то же самое для своей базы.')

        source = Path(database['NAME'])
        if not source.exists():
            raise CommandError(f'Файл базы не найден: {source}')

        root = Path(options['to']) if options['to'] \
            else Path(settings.BASE_DIR) / 'backups'
        target = self.free_folder(root, started)

        self.say(f'\n  Резервная копия от {started:%d.%m.%Y %H:%M}')
        self.say(f'  Куда: {target}\n')

        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise CommandError(f'Не удалось создать папку {target}: {e}')

        copy = self.copy_database(source, target)
        self.verify_database(source, copy)
        if not options['no_media']:
            self.copy_media(target)
        self.copy_settings(target)
        self.write_note(target, started, source)

        removed = self.remove_old(root, options['keep'])

        size = sum(f.stat().st_size for f in target.rglob('*') if f.is_file())
        self.say('')
        self.ok(f'Готово. Размер копии — {self.human_size(size)}')
        if removed:
            self.note(f'Удалено старых копий: {removed}')
        self.say('')

    # --- шаги -----------------------------------------------------------
    @staticmethod
    def free_folder(root, moment):
        """Свободная папка под копию.

        Имя — время снятия с точностью до секунды. Две копии подряд
        успевают попасть в одну секунду, и вторая молча легла бы поверх
        первой: человек думает, что у него две копии, а она одна.
        В таком случае к имени добавляется номер.
        """
        base = moment.strftime(STAMP_FORMAT)
        candidate = root / base
        number = 2
        while candidate.exists():
            candidate = root / f'{base}-{number}'
            number += 1
        return candidate

    def copy_database(self, source, target):
        """Снять копию базы средствами SQLite."""
        copy = target / source.name
        # VACUUM INTO отказывается писать поверх существующего файла —
        # убираем остаток от прошлой неудачной попытки.
        if copy.exists():
            copy.unlink()
        try:
            connection = sqlite3.connect(str(source))
            try:
                connection.execute('VACUUM INTO ?', (str(copy),))
            finally:
                connection.close()
        except sqlite3.Error as e:
            raise CommandError(f'Не удалось скопировать базу: {e}')
        self.ok(f'База данных — {self.human_size(copy.stat().st_size)}')
        return copy

    def verify_database(self, source, copy):
        """Проверить, что копию можно открыть и в ней всё на месте.

        Без этой проверки можно месяцами хранить файлы, которые не
        откроются в нужный момент. Сверяем не только читаемость, но и
        число записей в каждой таблице: копия, потерявшая последние
        документы, выглядит целой.
        """
        try:
            connection = sqlite3.connect(str(copy))
            result = connection.execute('PRAGMA integrity_check').fetchone()
            if not result or result[0] != 'ok':
                raise CommandError(
                    f'Копия базы повреждена: {result[0] if result else "?"}')
            copy_counts = self.table_counts(connection)
            connection.close()
        except sqlite3.Error as e:
            raise CommandError(f'Копия базы не читается: {e}')

        original = sqlite3.connect(str(source))
        source_counts = self.table_counts(original)
        original.close()

        differences = [
            f'{table}: в базе {source_counts[table]}, в копии {copy_counts.get(table)}'
            for table in source_counts
            if copy_counts.get(table) != source_counts[table]
        ]
        if differences:
            raise CommandError(
                'Копия отличается от базы — не берите её за основу:\n    '
                + '\n    '.join(differences))

        total = sum(source_counts.values())
        self.ok(f'Копия проверена: {len(source_counts)} таблиц, '
                f'{total} записей — сходится с базой')

    @staticmethod
    def table_counts(connection):
        """Сколько записей в каждой таблице."""
        counts = {}
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()
        for (name,) in tables:
            # Имя таблицы подставляется в кавычках: оно приходит из самой
            # базы, но правило «не склеивать запрос руками» стоит держать.
            row = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()
            counts[name] = row[0]
        return counts

    def copy_media(self, target):
        """Скопировать фотографии товаров."""
        media = Path(settings.MEDIA_ROOT)
        if not media.exists():
            self.note('Фотографий нет — каталог media не создан')
            return
        destination = target / 'media'
        shutil.copytree(media, destination, dirs_exist_ok=True)
        count = sum(1 for f in destination.rglob('*') if f.is_file())
        self.ok(f'Фотографии товаров — {count} файлов')

    def copy_settings(self, target):
        """Скопировать .env: восстанавливать настройки по памяти неприятно."""
        env = Path(settings.BASE_DIR) / '.env'
        if env.exists():
            shutil.copy2(env, target / '.env')
            self.ok('Файл настроек .env')
        else:
            self.note('Файла .env нет — настройки берутся из окружения')

    def write_note(self, target, started, source):
        """Записка внутри копии: что это и как этим пользоваться.

        Через полгода никто не вспомнит, что лежит в папке с датой.
        """
        (target / 'ЧТО-ЭТО.txt').write_text(
            'Резервная копия системы складского учёта ООО «ЛЕКО»\n'
            f'Снята: {started:%d.%m.%Y в %H:%M}\n'
            f'Источник: {source}\n'
            '\n'
            'Что внутри:\n'
            f'  {source.name}  — база данных целиком\n'
            '  media\\        — фотографии товаров\n'
            '  .env          — настройки этой установки\n'
            '\n'
            'Как восстановить:\n'
            '  1. Остановите систему (закройте окно с сервером).\n'
            '  2. Скопируйте файл базы отсюда в папку программы\n'
            '     поверх существующего.\n'
            '  3. Так же верните media и .env, если они нужны.\n'
            '  4. Запустите систему и выполните проверку:\n'
            '     python manage.py checksetup\n'
            '\n'
            'Копия снята средством SQLite и проверена при создании:\n'
            'число записей в каждой таблице совпадало с базой.\n',
            encoding='utf-8')

    def remove_old(self, root, keep_days):
        """Убрать копии старше заданного срока.

        Самую свежую не трогаем никогда, даже если срок хранения ноль:
        остаться совсем без копий из-за настройки — худший исход.
        """
        if keep_days <= 0:
            return 0
        folders = sorted(
            (p for p in root.iterdir()
             if p.is_dir() and self.folder_date(p) is not None),
            key=lambda p: self.folder_date(p))
        if len(folders) <= 1:
            return 0

        edge = datetime.now() - timedelta(days=keep_days)
        removed = 0
        for folder in folders[:-1]:      # последнюю оставляем всегда
            if self.folder_date(folder) < edge:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        return removed

    @staticmethod
    def folder_date(path):
        """Дата из имени папки или None, если имя чужое.

        По имени, а не по времени файла: время меняется при копировании
        папки на другой диск, и тогда старые копии считались бы свежими.
        """
        try:
            return datetime.strptime(path.name, STAMP_FORMAT)
        except ValueError:
            return None

    @staticmethod
    def human_size(size):
        for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
            if size < 1024 or unit == 'ГБ':
                return f'{size:.0f} {unit}' if unit == 'Б' \
                    else f'{size:.1f} {unit}'
            size /= 1024
