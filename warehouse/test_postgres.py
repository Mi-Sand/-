"""Проверка перехода на PostgreSQL.

Часть проверок идёт только на самой PostgreSQL — на SQLite они
пропускаются. Так и задумано: переход проверяется на той базе, на
которую переходят, а не на её замене.

Запустить на PostgreSQL:

    DB_ENGINE=postgres DB_NAME=... DB_USER=... DB_PASSWORD=... \\
        python manage.py test warehouse.test_postgres
"""
import sqlite3
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import skipIf, skipUnless

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.conf import settings
from django.db import connection
from django.test import TestCase, TransactionTestCase

from warehouse.management.commands.backup import Command as BackupCommand
from warehouse.models import Material, Supplier, Warehouse

User = get_user_model()

POSTGRES = connection.vendor == 'postgresql'


class DumpCountsTest(TestCase):
    """Разбор выгрузки pg_dump — на нём держится проверка копии.

    Считать строки в блоках COPY приходится самим: открыть выгрузку как
    базу нельзя, а копия, потерявшая последние документы, выглядит
    совершенно целой.
    """

    def dump(self, text):
        folder = Path(tempfile.mkdtemp())
        path = folder / 'db.sql'
        path.write_text(text, encoding='utf-8')
        return path

    def test_counts_rows_between_copy_and_dot(self):
        path = self.dump(
            'SET statement_timeout = 0;\n'
            'COPY public.materials (id, name) FROM stdin;\n'
            '1\tКожа\n'
            '2\tНитки\n'
            '3\tЗамша\n'
            '\\.\n'
            '\n'
            'COPY public.warehouses (id, name) FROM stdin;\n'
            '1\tОсновной\n'
            '\\.\n')
        self.assertEqual(BackupCommand.dump_counts(path),
                         {'materials': 3, 'warehouses': 1})

    def test_empty_table_is_counted_as_zero(self):
        path = self.dump('COPY public.suppliers (id) FROM stdin;\n\\.\n')
        self.assertEqual(BackupCommand.dump_counts(path), {'suppliers': 0})

    def test_dump_without_data_is_empty(self):
        path = self.dump('CREATE TABLE materials (id integer);\n')
        self.assertEqual(BackupCommand.dump_counts(path), {})

    def test_words_outside_copy_are_not_taken_for_data(self):
        """Строка «COPY» внутри комментария не должна сбивать счёт."""
        path = self.dump(
            'COPY public.materials (id, name) FROM stdin;\n'
            '1\tшланг для COPY\n'
            '\\.\n')
        self.assertEqual(BackupCommand.dump_counts(path), {'materials': 1})


class MoveCommandGuardTest(TestCase):
    """Перенос отказывается работать там, где он ничего хорошего не даст."""

    @skipIf(POSTGRES, 'проверяется поведение на SQLite')
    def test_refuses_when_target_is_not_postgres(self):
        with self.assertRaises(CommandError) as caught:
            call_command('topostgres', stdout=StringIO())
        self.assertIn('PostgreSQL', str(caught.exception))

    @skipUnless(POSTGRES, 'нужна PostgreSQL')
    def test_missing_source_is_explained(self):
        with self.assertRaises(CommandError) as caught:
            call_command('topostgres', **{'from': '/нет/такого.sqlite3'},
                         stdout=StringIO())
        self.assertIn('не найдена', str(caught.exception))


@skipUnless(POSTGRES, 'нужна PostgreSQL')
class MoveDataTest(TestCase):
    """Перенос не должен затирать базу, в которой уже работают."""

    def test_refuses_when_target_has_data(self):
        Warehouse.objects.create(name='Основной', type='raw')
        source = Path(tempfile.mkdtemp()) / 'db.sqlite3'
        sqlite3.connect(str(source)).close()
        with self.assertRaises(CommandError) as caught:
            call_command('topostgres', **{'from': str(source)},
                         stdout=StringIO())
        message = str(caught.exception)
        self.assertIn('уже есть данные', message)
        # Подсказка, что делать дальше, важнее самого отказа
        self.assertIn('flush', message)


@skipUnless(POSTGRES, 'нужна PostgreSQL')
class PostgresBackupTest(TransactionTestCase):
    """Копия с PostgreSQL снимается и проверяется."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        Warehouse.objects.create(name='Основной', type='raw')
        Material.objects.create(name='Кожа хромовая', unit='m',
                                category='leather',
                                reorder_point=Decimal('5'))
        Supplier.objects.create(name='ООО Поставка')

    def test_backup_makes_a_readable_dump(self):
        out = StringIO()
        call_command('backup', to=self.folder.name, keep=0, no_media=True,
                     stdout=out, stderr=out)
        folders = [p for p in Path(self.folder.name).iterdir() if p.is_dir()]
        self.assertEqual(len(folders), 1)
        dump = folders[0] / 'db.sql'
        self.assertTrue(dump.exists())
        self.assertIn('Копия проверена', out.getvalue())

    def test_dump_holds_the_data(self):
        call_command('backup', to=self.folder.name, keep=0, no_media=True,
                     stdout=StringIO(), stderr=StringIO())
        folder = [p for p in Path(self.folder.name).iterdir() if p.is_dir()][0]
        text = (folder / 'db.sql').read_text(encoding='utf-8')
        self.assertIn('Кожа хромовая', text)
        self.assertIn('ООО Поставка', text)

    def test_note_explains_restore_for_postgres(self):
        """У PostgreSQL порядок восстановления свой — psql, а не копия."""
        call_command('backup', to=self.folder.name, keep=0, no_media=True,
                     stdout=StringIO(), stderr=StringIO())
        folder = [p for p in Path(self.folder.name).iterdir() if p.is_dir()][0]
        note = (folder / 'ЧТО-ЭТО.txt').read_text(encoding='utf-8')
        self.assertIn('psql', note)
        self.assertIn('createdb', note)

    def test_shortened_dump_is_caught(self):
        """Главная проверка: копия, потерявшая записи, должна отлавливаться."""
        call_command('backup', to=self.folder.name, keep=0, no_media=True,
                     stdout=StringIO(), stderr=StringIO())
        folder = [p for p in Path(self.folder.name).iterdir() if p.is_dir()][0]
        dump = folder / 'db.sql'

        lines, kept, dropped = dump.read_text(encoding='utf-8').split('\n'), [], 0
        inside = False
        for line in lines:
            if line.startswith('COPY ') and 'materials' in line:
                inside = True
            elif inside and line.startswith('\\.'):
                inside = False
            elif inside and dropped == 0 and line.strip():
                dropped += 1
                continue
            kept.append(line)
        dump.write_text('\n'.join(kept), encoding='utf-8')
        self.assertEqual(dropped, 1, 'из копии не убрали ни одной записи')

        command = BackupCommand()
        command.quiet = True
        with self.assertRaises(CommandError) as caught:
            command.verify_postgres_dump(dump)
        self.assertIn('materials', str(caught.exception))


@skipIf(POSTGRES, 'проверяются настройки SQLite')
class SqliteTuningTest(TestCase):
    """Настройки SQLite, ради которых всё и мерялось.

    Проверяются сами настройки, а не текущее подключение: тестовая база
    у Django живёт в памяти, а база в памяти WAL не поддерживает —
    проверка подключения говорила бы о тесте, а не о рабочей системе.
    """

    def options(self):
        return settings.DATABASES['default'].get('OPTIONS', {})

    def test_write_ahead_log_is_on(self):
        """Без WAL отчёт держал базу, и проведение документа ждало его.

        На проверке (три отчёта и проведение документов, пять секунд,
        отдельными процессами): без WAL — 2434 документа и самый долгий
        отчёт 2,24 с; с WAL — 7157 документов и 0,02 с.
        """
        self.assertIn('journal_mode=WAL',
                      self.options().get('init_command', ''))

    def test_waits_instead_of_refusing(self):
        """Занятая база должна заставить подождать, а не ответить отказом."""
        self.assertGreaterEqual(self.options().get('timeout', 0), 5)

    def test_transaction_takes_the_write_lock_at_once(self):
        """Иначе два проведения столкнулись бы на полпути."""
        self.assertEqual(self.options().get('transaction_mode'), 'IMMEDIATE')
