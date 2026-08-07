"""Проверка команды `manage.py backup`.

Копия, которую нельзя восстановить, хуже её отсутствия: на неё
рассчитывают. Поэтому здесь проверяется не только то, что файлы
создаются, но и то, что негодная копия будет распознана.
"""
import sqlite3
import tempfile
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from warehouse.management.commands.backup import Command, STAMP_FORMAT


def run(**options):
    out = StringIO()
    call_command('backup', stdout=out, stderr=out, **options)
    return out.getvalue()


def make_database(path):
    """Небольшая база с данными — на ней и проверяем копирование."""
    connection = sqlite3.connect(str(path))
    connection.execute('CREATE TABLE goods (id INTEGER, name TEXT)')
    connection.execute('CREATE TABLE moves (id INTEGER, qty REAL)')
    connection.executemany('INSERT INTO goods VALUES (?, ?)',
                           [(i, f'позиция {i}') for i in range(20)])
    connection.executemany('INSERT INTO moves VALUES (?, ?)',
                           [(i, i * 1.5) for i in range(50)])
    connection.commit()
    connection.close()


class BackupTest(TestCase):
    """Копия снимается и содержит то, что должна.

    Тесты идут на своей базе-файле, а не на той, что у Django в памяти:
    команда копирует файл, и файл ей нужен настоящий. Заодно так видно
    только своё — чужие данные в проверку не попадают.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'копии'
        self.root.mkdir()

        self.database = base / 'db.sqlite3'
        make_database(self.database)

        self.project = base / 'программа'
        (self.project / 'media').mkdir(parents=True)
        (self.project / 'media' / 'снимок.jpg').write_bytes(b'0' * 10)
        (self.project / '.env').write_text('DEBUG=False\n', encoding='utf-8')

        patch = override_settings(
            DATABASES={'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': str(self.database)}},
            MEDIA_ROOT=str(self.project / 'media'),
            BASE_DIR=str(self.project))
        patch.enable()
        self.addCleanup(patch.disable)

    def folders(self):
        return sorted(p for p in self.root.iterdir() if p.is_dir())

    def test_backup_creates_folder_with_database(self):
        run(to=str(self.root), quiet=True)
        folders = self.folders()
        self.assertEqual(len(folders), 1)
        self.assertTrue((folders[0] / 'db.sqlite3').exists())

    def test_backup_includes_explanation(self):
        """Через полгода никто не вспомнит, что в папке с датой."""
        run(to=str(self.root), quiet=True)
        note = (self.folders()[0] / 'ЧТО-ЭТО.txt').read_text(encoding='utf-8')
        self.assertIn('Как восстановить', note)
        self.assertIn('checksetup', note)

    def test_folder_name_is_a_readable_date(self):
        run(to=str(self.root), quiet=True)
        name = self.folders()[0].name
        self.assertIsNotNone(datetime.strptime(name, STAMP_FORMAT))

    def test_copy_opens_and_holds_the_same_data(self):
        """Смысл всей затеи: копия должна открываться и быть полной."""
        run(to=str(self.root), quiet=True)
        copy = self.folders()[0] / 'db.sqlite3'
        connection = sqlite3.connect(str(copy))
        goods = connection.execute('SELECT COUNT(*) FROM goods').fetchone()[0]
        moves = connection.execute('SELECT COUNT(*) FROM moves').fetchone()[0]
        connection.close()
        self.assertEqual((goods, moves), (20, 50))

    def test_two_backups_do_not_collide(self):
        """Две копии подряд должны лечь в разные папки."""
        run(to=str(self.root), quiet=True)
        run(to=str(self.root), quiet=True)
        self.assertEqual(len(self.folders()), 2)

    def test_output_says_what_was_done(self):
        output = run(to=str(self.root))
        self.assertIn('База данных', output)
        self.assertIn('Копия проверена', output)

    def test_media_and_settings_are_copied(self):
        run(to=str(self.root), quiet=True)
        folder = self.folders()[0]
        self.assertTrue((folder / 'media' / 'снимок.jpg').exists())
        self.assertTrue((folder / '.env').exists())

    def test_no_media_skips_photos(self):
        run(to=str(self.root), quiet=True, no_media=True)
        folder = self.folders()[0]
        self.assertFalse((folder / 'media').exists())
        # настройки копируются всегда: они маленькие, а восстанавливать
        # их по памяти неприятно
        self.assertTrue((folder / '.env').exists())


class BackupVerificationTest(TestCase):
    """Негодная копия должна распознаваться при создании, а не при беде."""

    def setUp(self):
        self.command = Command()
        self.command.quiet = True
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source.sqlite3'
        connection = sqlite3.connect(str(self.source))
        connection.execute('CREATE TABLE goods (id INTEGER, name TEXT)')
        connection.executemany('INSERT INTO goods VALUES (?, ?)',
                               [(i, f'позиция {i}') for i in range(20)])
        connection.commit()
        connection.close()

    def good_copy(self):
        copy = Path(self.temp.name) / 'copy.sqlite3'
        connection = sqlite3.connect(str(self.source))
        connection.execute('VACUUM INTO ?', (str(copy),))
        connection.close()
        return copy

    def test_good_copy_passes(self):
        self.command.verify_database(self.source, self.good_copy())

    def test_unreadable_file_is_caught(self):
        broken = Path(self.temp.name) / 'broken.sqlite3'
        broken.write_bytes(b'SQLite format 3\x00' + b'\x00' * 2000)
        with self.assertRaises(CommandError):
            self.command.verify_database(self.source, broken)

    def test_missing_records_are_caught(self):
        """Главная проверка.

        Копия, потерявшая часть записей, остаётся исправным файлом:
        встроенная проверка SQLite скажет «ok». Поймать такую копию
        может только сверка числа записей с базой — ради неё всё и
        затевалось.
        """
        copy = self.good_copy()
        connection = sqlite3.connect(str(copy))
        connection.execute('DELETE FROM goods WHERE id < 3')
        connection.commit()
        state = connection.execute('PRAGMA integrity_check').fetchone()[0]
        connection.close()

        self.assertEqual(state, 'ok', 'файл цел — тем и опасен')
        with self.assertRaises(CommandError) as caught:
            self.command.verify_database(self.source, copy)
        self.assertIn('goods', str(caught.exception))


class BackupRetentionTest(TestCase):
    """Старые копии убираются, но не последняя."""

    def setUp(self):
        self.command = Command()
        self.command.quiet = True
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def make(self, days_ago):
        moment = datetime.now() - timedelta(days=days_ago)
        folder = self.root / moment.strftime(STAMP_FORMAT)
        folder.mkdir()
        return folder

    def test_old_folders_removed(self):
        old = self.make(60)
        fresh = self.make(1)
        self.command.remove_old(self.root, keep_days=30)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_newest_is_never_removed(self):
        """Даже если все копии старые, одна должна остаться.

        Остаться совсем без копий из-за настройки хранения — худший
        исход, какой может быть у средства резервного копирования.
        """
        older = self.make(400)
        newest = self.make(365)
        self.command.remove_old(self.root, keep_days=30)
        self.assertFalse(older.exists())
        self.assertTrue(newest.exists())

    def test_zero_keeps_everything(self):
        old = self.make(1000)
        self.command.remove_old(self.root, keep_days=0)
        self.assertTrue(old.exists())

    def test_foreign_folders_are_left_alone(self):
        """Чужая папка в каталоге копий не наша забота — не трогаем."""
        foreign = self.root / 'мои документы'
        foreign.mkdir()
        self.make(1)
        self.command.remove_old(self.root, keep_days=1)
        self.assertTrue(foreign.exists())

    def test_date_read_from_name_not_file_time(self):
        """Дата берётся из имени: время файла меняется при копировании."""
        folder = self.make(5)
        self.assertIsNotNone(self.command.folder_date(folder))
        self.assertIsNone(self.command.folder_date(self.root / 'что-то'))


class BackupOnPostgresTest(TestCase):
    """С PostgreSQL команда должна честно отказаться, а не сделать вид."""

    @override_settings(DATABASES={'default': {
        'ENGINE': 'django.db.backends.postgresql', 'NAME': 'warehouse'}})
    def test_refuses_and_explains(self):
        with self.assertRaises(CommandError) as caught:
            call_command('backup', quiet=True)
        self.assertIn('pg_dump', str(caught.exception))
