"""Восстановление системы из резервной копии.

    python manage.py restore                    # из самой свежей копии
    python manage.py restore --from D:\\Копии\\Склад\\2026-08-11_23-30

Обратная сторона команды `backup`. Копии снимаются каждую ночь, но
копия, которую ни разу не разворачивали, — это надежда, а не гарантия:
проверять её в день беды поздно.

Порядок восстановления записан здесь, а не только в документации,
потому что делать это будут в худший день года — когда база испорчена,
работа стоит, и читать полстраницы текста некому. Команда сама найдёт
свежую копию, проверит её до подмены, сохранит то, что есть сейчас, и
скажет, что получилось.

Чего команда не делает: не ставит систему на чистый компьютер. Для
этого нужен Python и окружение, и порядок описан в ВОССТАНОВЛЕНИЕ.md.
Здесь — возврат данных в уже поставленную систему.
"""
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from .backup import STAMP_FORMAT, Command as BackupCommand


class Command(BaseCommand):
    help = 'Восстановить базу и фотографии из резервной копии'

    def add_arguments(self, parser):
        parser.add_argument(
            '--from', dest='source', default=None, metavar='ПАПКА',
            help='Папка с копией. По умолчанию — самая свежая из backups '
                 'рядом с программой.')
        parser.add_argument(
            '--in', dest='root', default=None, metavar='ПАПКА',
            help='Где искать копии, если они лежат не в backups '
                 '(например, D:\\Копии\\Склад).')
        parser.add_argument(
            '--no-media', action='store_true',
            help='Не возвращать фотографии товаров — только базу.')
        parser.add_argument(
            '--yes', action='store_true',
            help='Не спрашивать подтверждения. Для сценариев.')

    # --- вывод ----------------------------------------------------------
    def say(self, text=''):
        self.stdout.write(text)

    def ok(self, text):
        self.stdout.write(self.style.SUCCESS(f'  {text}'))

    def note(self, text):
        self.stdout.write(f'  {text}')

    # --- основное -------------------------------------------------------
    def handle(self, *args, **options):
        database = settings.DATABASES['default']
        if 'sqlite' not in database['ENGINE']:
            raise CommandError(
                'Эта команда возвращает из копии только базу SQLite.\n'
                '    Для PostgreSQL восстановление идёт средствами самой '
                'базы — порядок\n'
                '    записан в файле ЧТО-ЭТО.txt внутри копии и в '
                'ВОССТАНОВЛЕНИЕ.md.')

        folder = self.choose_folder(options)
        copy = folder / Path(database['NAME']).name
        if not copy.exists():
            # Копия могла быть снята под другим именем файла базы
            found = sorted(folder.glob('*.sqlite3'))
            if not found:
                if any(folder.glob('*.sql')):
                    raise CommandError(
                        f'В папке {folder} лежит копия базы PostgreSQL, '
                        f'а система работает на SQLite.\n'
                        f'    Такую копию разворачивают средствами самой '
                        f'PostgreSQL — порядок записан\n'
                        f'    в файле ЧТО-ЭТО.txt внутри копии.')
                raise CommandError(
                    f'В папке {folder} нет файла базы. Это точно копия?')
            copy = found[0]

        self.say(f'\n  Восстановление из {folder}')
        self.check_copy(copy)

        target = Path(database['NAME'])
        if not options['yes']:
            self.confirm(target)

        keep = self.save_current(target)
        if keep:
            self.note(f'Прежняя база сохранена: {keep.name}')

        self.put_database(copy, target)
        if not options['no_media']:
            self.put_media(folder)
        self.after(target)

    # --- шаги -----------------------------------------------------------
    def choose_folder(self, options):
        """Какую копию разворачиваем."""
        if options['source']:
            folder = Path(options['source'])
            if not folder.is_dir():
                raise CommandError(f'Папка с копией не найдена: {folder}')
            return folder

        root = Path(options['root']) if options['root'] \
            else Path(settings.BASE_DIR) / 'backups'
        if not root.is_dir():
            raise CommandError(
                f'Папка с копиями не найдена: {root}\n'
                f'    Укажите её прямо: --in D:\\Копии\\Склад')

        # Папки без файла базы пропускаем: это либо след неудавшегося
        # копирования, либо чужая папка. Взять такую «самой свежей» —
        # значит отказаться от восстановления, имея на руках рабочую
        # копию за предыдущий день.
        folders = sorted(
            (path for path in root.iterdir()
             if path.is_dir() and self.folder_date(path)
             and (any(path.glob('*.sqlite3')) or any(path.glob('*.sql')))),
            key=self.folder_date)
        if not folders:
            raise CommandError(
                f'В {root} нет ни одной копии с базой.\n'
                f'    Копии называются по времени снятия, например '
                f'2026-08-11_23-30-00,\n'
                f'    и содержат файл базы внутри.')
        newest = folders[-1]
        self.note(f'Взята самая свежая копия из {len(folders)}: {newest.name}')
        return newest

    @staticmethod
    def folder_date(path):
        try:
            return datetime.strptime(path.name[:19], STAMP_FORMAT)
        except ValueError:
            return None

    def check_copy(self, copy):
        """Проверить копию до того, как ею заменят рабочую базу.

        Разворачивать непроверенную копию — верный способ остаться и
        без копии, и без базы: если файл окажется битым, прежней базы
        уже не будет.
        """
        try:
            connection = sqlite3.connect(str(copy))
            state = connection.execute('PRAGMA integrity_check').fetchone()
            if not state or state[0] != 'ok':
                raise CommandError(
                    f'Копия повреждена: {state[0] if state else "?"}. '
                    f'Возьмите копию за предыдущий день.')
            counts = BackupCommand.table_counts(connection)
            connection.close()
        except sqlite3.Error as e:
            raise CommandError(
                f'Копия не открывается: {e}. Возьмите копию за '
                f'предыдущий день.')

        total = sum(counts.values())
        if not total:
            raise CommandError(
                'В копии нет ни одной записи. Разворачивать её незачем.')
        self.ok(f'Копия проверена: {len(counts)} таблиц, {total} записей')

        size = copy.stat().st_size
        self.note(f'Размер базы в копии — {BackupCommand.human_size(size)}')

    def confirm(self, target):
        """Спросить подтверждение — но только если есть у кого спросить.

        Команду запускают и из сценариев, и из планировщика, где ввода
        нет вовсе. Обычный input() в таком запуске повис бы навсегда, и
        со стороны это выглядело бы как зависшее восстановление —
        худшее, что можно предложить человеку в этот момент.
        """
        if not sys.stdin or not sys.stdin.isatty():
            raise CommandError(
                'Восстановление заменяет нынешнюю базу, и без ответа '
                'человека команда этого не делает.\n'
                '    Запуск идёт без ввода (сценарий или задание), '
                'поэтому подтвердите заранее:\n'
                '      python manage.py restore --yes')

        answer = input(
            f'\n  Заменить нынешнюю базу {target.name} содержимым копии?\n'
            f'  Напишите ДА, чтобы продолжить: ')
        if answer.strip() != 'ДА':
            raise CommandError('Отменено — ничего не тронуто.')

    def save_current(self, target):
        """Отложить нынешнюю базу в сторону, а не затереть.

        Восстанавливают обычно в спешке и иногда не ту копию. Прежний
        файл остаётся рядом под именем с отметкой времени: вернуть его
        обратно можно переименованием.
        """
        if not target.exists():
            return None
        stamp = datetime.now().strftime(STAMP_FORMAT)
        keep = target.with_name(f'{target.name}.до-восстановления-{stamp}')
        shutil.copy2(target, keep)
        return keep

    def put_database(self, copy, target):
        """Положить базу из копии на место рабочей."""
        # Подключения закрываем: на Windows открытый файл не заменить,
        # а на любой системе оставленное подключение продолжит писать
        # в старую базу.
        connections.close_all()

        # Журнал прежней базы (файлы -wal и -shm) убираем до подмены:
        # оставленный, он допишется поверх копии, и в базе окажется
        # смесь старого с новым. База работает в режиме WAL, так что
        # эти файлы есть почти всегда.
        for tail in ('-wal', '-shm'):
            sidecar = target.with_name(target.name + tail)
            if sidecar.exists():
                sidecar.unlink()

        shutil.copy2(copy, target)
        self.ok(f'База возвращена из копии: {target.name}')

    def put_media(self, folder):
        """Вернуть фотографии товаров."""
        source = folder / 'media'
        if not source.is_dir():
            self.note('Фотографий в копии нет — пропущено')
            return
        destination = Path(settings.MEDIA_ROOT)
        destination.mkdir(parents=True, exist_ok=True)
        # Копируем поверх, а не заменяя каталог целиком: снимки,
        # добавленные после копии, при замене исчезли бы, а так
        # останутся.
        shutil.copytree(source, destination, dirs_exist_ok=True)
        count = sum(1 for path in source.rglob('*') if path.is_file())
        self.ok(f'Фотографии товаров возвращены: {count} файлов')

    def after(self, target):
        """Сказать, что получилось, и что делать дальше."""
        connections.close_all()
        connection = sqlite3.connect(str(target))
        try:
            counts = BackupCommand.table_counts(connection)
        finally:
            connection.close()

        interesting = [
            ('materials', 'материалов'),
            ('products', 'продукции'),
            ('warehouses', 'складов'),
            ('stock', 'остатков'),
            ('stock_movements', 'движений'),
            ('orders', 'заказов'),
        ]
        parts = [f'{title} {counts[table]}'
                 for table, title in interesting if table in counts]

        self.say('')
        self.stdout.write(self.style.SUCCESS('  Восстановление завершено'))
        if parts:
            self.note('В базе: ' + ' | '.join(parts))
        self.say('')
        self.say('  Дальше:')
        self.say('    1. python manage.py checksetup')
        self.say('    2. Запустите систему и откройте пару рабочих страниц —')
        self.say('       остатки, приход, отчёты.')
        self.say('    3. Убедившись, что всё на месте, удалите файл')
        self.say('       db.sqlite3.до-восстановления-… рядом с базой.')
        self.say('')
