"""Проверка команды `manage.py restore`.

Восстановление делают в худший день года: база испорчена, работа стоит,
разбираться некогда. Поэтому здесь проверяется не только то, что данные
возвращаются, но и то, что команда не сделает хуже: не развернёт битую
копию, не затрёт прежнюю базу без следа, не оставит рядом старый
журнал.
"""
import sqlite3
import tempfile
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from warehouse.management.commands.backup import STAMP_FORMAT


def make_database(path, goods=20):
    connection = sqlite3.connect(str(path))
    connection.execute('CREATE TABLE materials (id INTEGER, name TEXT)')
    connection.executemany('INSERT INTO materials VALUES (?, ?)',
                           [(i, f'материал {i}') for i in range(goods)])
    connection.commit()
    connection.close()


def count(path, table='materials'):
    connection = sqlite3.connect(str(path))
    try:
        return connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    finally:
        connection.close()


class RestoreTest(TestCase):
    """Возврат данных из копии."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)

        self.project = base / 'программа'
        self.project.mkdir()
        self.database = self.project / 'db.sqlite3'
        make_database(self.database, goods=3)          # «нынешняя» база

        self.root = base / 'копии'
        self.root.mkdir()
        self.copy_folder = self.make_copy('2026-08-10_23-30-00', goods=20)

        patch = override_settings(
            DATABASES={'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': str(self.database)}},
            MEDIA_ROOT=str(self.project / 'media'),
            BASE_DIR=str(self.project))
        patch.enable()
        self.addCleanup(patch.disable)

    def make_copy(self, name, goods=20, with_media=True):
        folder = self.root / name
        folder.mkdir()
        make_database(folder / 'db.sqlite3', goods=goods)
        if with_media:
            (folder / 'media').mkdir()
            (folder / 'media' / 'снимок.jpg').write_bytes(b'0' * 10)
        return folder

    def run_restore(self, **options):
        out = StringIO()
        call_command('restore', stdout=out, stderr=out, yes=True, **options)
        return out.getvalue()

    def test_database_comes_back(self):
        self.assertEqual(count(self.database), 3)
        self.run_restore(**{'in': str(self.root)})
        self.assertEqual(count(self.database), 20)

    def test_newest_copy_is_taken(self):
        """Из нескольких копий берётся свежая, а не первая попавшаяся."""
        self.make_copy('2026-08-11_23-30-00', goods=42)
        output = self.run_restore(**{'in': str(self.root)})
        self.assertIn('2026-08-11_23-30-00', output)
        self.assertEqual(count(self.database), 42)

    def test_given_copy_is_used(self):
        older = self.make_copy('2026-01-01_10-00-00', goods=7)
        self.make_copy('2026-08-12_23-30-00', goods=99)
        self.run_restore(**{'from': str(older)})
        self.assertEqual(count(self.database), 7)

    def test_previous_database_is_kept(self):
        """Прежнюю базу откладывают в сторону, а не затирают.

        Восстанавливают в спешке и иногда не ту копию: без этого файла
        вернуться было бы некуда.
        """
        self.run_restore(**{'in': str(self.root)})
        kept = list(self.project.glob('db.sqlite3.до-восстановления-*'))
        self.assertEqual(len(kept), 1)
        self.assertEqual(count(kept[0]), 3)

    def test_media_comes_back(self):
        self.run_restore(**{'in': str(self.root)})
        self.assertTrue(
            (self.project / 'media' / 'снимок.jpg').exists())

    def test_no_media_leaves_photos_alone(self):
        self.run_restore(**{'in': str(self.root)}, no_media=True)
        self.assertFalse((self.project / 'media' / 'снимок.jpg').exists())
        self.assertEqual(count(self.database), 20)

    def test_newer_photos_are_not_lost(self):
        """Снимок, добавленный после копии, должен остаться.

        Каталог фотографий пополняется поверх, а не заменяется целиком:
        база и снимки живут своей жизнью, и терять свежие ради старой
        копии незачем.
        """
        media = self.project / 'media'
        media.mkdir(parents=True, exist_ok=True)
        (media / 'вчерашний.jpg').write_bytes(b'1' * 5)
        self.run_restore(**{'in': str(self.root)})
        self.assertTrue((media / 'вчерашний.jpg').exists())
        self.assertTrue((media / 'снимок.jpg').exists())

    def test_without_confirmation_it_does_not_wait_forever(self):
        """Запуск без ввода — понятный отказ, а не молчаливое ожидание.

        Команду запускают и из сценариев, и из планировщика. Обычный
        запрос подтверждения там повис бы навсегда, и со стороны это
        выглядело бы как зависшее восстановление.
        """
        with self.assertRaises(CommandError) as caught:
            call_command('restore', **{'in': str(self.root)},
                         stdout=StringIO(), stderr=StringIO())
        self.assertIn('--yes', str(caught.exception))
        # База при этом не тронута
        self.assertEqual(count(self.database), 3)

    def test_stale_journal_is_removed(self):
        """Журнал прежней базы рядом с копией — это смесь старого с новым.

        База работает в режиме WAL, поэтому файлы -wal и -shm есть
        почти всегда. Оставленный журнал допишется поверх копии.
        """
        for tail in ('-wal', '-shm'):
            self.database.with_name(self.database.name + tail).write_bytes(
                'мусор'.encode('utf-8'))
        self.run_restore(**{'in': str(self.root)})
        for tail in ('-wal', '-shm'):
            self.assertFalse(
                self.database.with_name(self.database.name + tail).exists())


class RestoreRefusesTest(TestCase):
    """Когда команда должна отказаться, а не сделать хуже."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.project = base / 'программа'
        self.project.mkdir()
        self.database = self.project / 'db.sqlite3'
        make_database(self.database, goods=5)
        self.root = base / 'копии'
        self.root.mkdir()

        patch = override_settings(
            DATABASES={'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': str(self.database)}},
            MEDIA_ROOT=str(self.project / 'media'),
            BASE_DIR=str(self.project))
        patch.enable()
        self.addCleanup(patch.disable)

    def restore(self, **options):
        call_command('restore', stdout=StringIO(), stderr=StringIO(),
                     yes=True, **options)

    def test_broken_copy_is_refused(self):
        """Главная проверка.

        Развернуть битую копию — остаться и без копии, и без базы:
        прежней уже не будет. Поэтому копия проверяется до подмены.
        """
        folder = self.root / '2026-08-10_23-30-00'
        folder.mkdir()
        (folder / 'db.sqlite3').write_bytes(
            b'SQLite format 3\x00' + b'\x17' * 4000)

        with self.assertRaises(CommandError) as caught:
            self.restore(**{'in': str(self.root)})
        self.assertIn('копи', str(caught.exception).lower())
        # И база на месте — нетронутая
        self.assertEqual(count(self.database), 5)

    def test_empty_copy_is_refused(self):
        folder = self.root / '2026-08-10_23-30-00'
        folder.mkdir()
        connection = sqlite3.connect(str(folder / 'db.sqlite3'))
        connection.execute('CREATE TABLE materials (id INTEGER)')
        connection.commit()
        connection.close()

        with self.assertRaises(CommandError) as caught:
            self.restore(**{'in': str(self.root)})
        self.assertIn('ни одной записи', str(caught.exception))
        self.assertEqual(count(self.database), 5)

    def test_folder_without_database_is_skipped(self):
        """Пустая папка с датой — след неудавшегося копирования.

        Взять её «самой свежей» значило бы отказаться от
        восстановления, имея на руках рабочую копию за предыдущий день.
        """
        (self.root / '2026-08-11_23-30-00').mkdir()      # пустая, свежая
        good = self.root / '2026-08-10_23-30-00'
        good.mkdir()
        make_database(good / 'db.sqlite3', goods=12)

        self.restore(**{'in': str(self.root)})
        self.assertEqual(count(self.database), 12)

    def test_named_folder_without_database_is_explained(self):
        folder = self.root / '2026-08-10_23-30-00'
        folder.mkdir()
        (folder / 'ЧТО-ЭТО.txt').write_text('пусто', encoding='utf-8')
        with self.assertRaises(CommandError) as caught:
            self.restore(**{'from': str(folder)})
        self.assertIn('нет файла базы', str(caught.exception))

    def test_postgres_copy_is_recognised(self):
        """Копия PostgreSQL в папке — не повод говорить «это точно копия?».

        В общей папке копий могут лежать обе: система переезжала с
        одной базы на другую. Сказать про это прямо полезнее, чем
        отказать непонятно.
        """
        folder = self.root / '2026-08-10_23-30-00'
        folder.mkdir()
        (folder / 'db.sql').write_text('COPY public.materials ...',
                                       encoding='utf-8')
        with self.assertRaises(CommandError) as caught:
            self.restore(**{'from': str(folder)})
        self.assertIn('PostgreSQL', str(caught.exception))

    def test_no_copies_at_all(self):
        with self.assertRaises(CommandError) as caught:
            self.restore(**{'in': str(self.root)})
        self.assertIn('нет ни одной копии', str(caught.exception))

    def test_missing_folder_is_explained(self):
        with self.assertRaises(CommandError) as caught:
            self.restore(**{'from': '/нет/такой/папки'})
        self.assertIn('не найдена', str(caught.exception))

    def test_foreign_folders_are_ignored(self):
        """Чужая папка среди копий не должна приниматься за копию."""
        (self.root / 'мои документы').mkdir()
        with self.assertRaises(CommandError) as caught:
            self.restore(**{'in': str(self.root)})
        self.assertIn('нет ни одной копии', str(caught.exception))

    @override_settings(DATABASES={'default': {
        'ENGINE': 'django.db.backends.postgresql', 'NAME': 'warehouse'}})
    def test_postgres_is_sent_to_its_own_tools(self):
        with self.assertRaises(CommandError) as caught:
            self.restore(**{'in': str(self.root)})
        self.assertIn('PostgreSQL', str(caught.exception))


class RestoreAfterBackupTest(TestCase):
    """Копия, снятая командой backup, должна разворачиваться командой restore.

    Порознь обе работают; смысл имеет только связка.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.project = base / 'программа'
        (self.project / 'media').mkdir(parents=True)
        (self.project / 'media' / 'снимок.jpg').write_bytes(b'0' * 10)
        self.database = self.project / 'db.sqlite3'
        make_database(self.database, goods=25)
        self.root = base / 'копии'

        patch = override_settings(
            DATABASES={'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': str(self.database)}},
            MEDIA_ROOT=str(self.project / 'media'),
            BASE_DIR=str(self.project))
        patch.enable()
        self.addCleanup(patch.disable)

    def test_backup_then_break_then_restore(self):
        call_command('backup', to=str(self.root), quiet=True,
                     stdout=StringIO(), stderr=StringIO())

        # Порча базы — как при отказе диска
        data = bytearray(self.database.read_bytes())
        for position in range(3000, min(len(data), 20000), 53):
            data[position] = 0x17
        self.database.write_bytes(bytes(data))
        with self.assertRaises(sqlite3.DatabaseError):
            count(self.database)

        call_command('restore', **{'in': str(self.root)}, yes=True,
                     stdout=StringIO(), stderr=StringIO())
        self.assertEqual(count(self.database), 25)

    def test_old_copy_is_still_restorable(self):
        """Копия недельной давности разворачивается так же, как вчерашняя."""
        call_command('backup', to=str(self.root), quiet=True,
                     stdout=StringIO(), stderr=StringIO())
        folder = next(path for path in self.root.iterdir() if path.is_dir())
        week_ago = datetime.now() - timedelta(days=7)
        folder.rename(self.root / week_ago.strftime(STAMP_FORMAT))

        self.database.unlink()
        make_database(self.database, goods=1)
        call_command('restore', **{'in': str(self.root)}, yes=True,
                     stdout=StringIO(), stderr=StringIO())
        self.assertEqual(count(self.database), 25)
