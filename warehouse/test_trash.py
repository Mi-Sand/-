"""Корзина удалённых документов.

Удаление раньше было окончательным: непроведённый документ исчезал
вместе со строками, и вернуть его было неоткуда, кроме резервной копии
за прошлую ночь. Удаляют же обычно второпях и не тот документ.

Здесь проверяется главное: удалённое не пропадает, из обычной работы
не выглядывает, возвращается целиком и вычищается только по сроку.
"""
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (InboundDocument, InboundItem, Material, OutboundDocument,
                     Supplier, Warehouse)
from .trash import items, purge

User = get_user_model()


class TrashTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='klad', password='x', role='storekeeper')
        self.client.force_login(self.user)
        self.warehouse = Warehouse.objects.create(name='Основной', type='raw')
        self.supplier = Supplier.objects.create(name='ООО Поставка')
        self.material = Material.objects.create(
            name='Кожа хромовая', unit='m', category='leather',
            reorder_point=Decimal('50'))

    def make_inbound(self, number='ПР-1', processed=False):
        document = InboundDocument.objects.create(
            doc_number=number, doc_date=date.today(), supplier=self.supplier,
            warehouse=self.warehouse, created_by=self.user,
            processed=processed)
        InboundItem.objects.create(
            inbound_doc=document, material=self.material,
            quantity=Decimal('10'), unit_price=Decimal('100'))
        return document


class DeletionTest(TrashTestBase):
    """Что происходит при удалении."""

    def test_document_is_not_lost(self):
        document = self.make_inbound()
        answer = self.client.delete(f'/api/inbound-documents/{document.pk}/')
        self.assertEqual(answer.status_code, 204)
        self.assertTrue(InboundDocument.all_objects.filter(
            pk=document.pk).exists())

    def test_lines_survive_too(self):
        """Строки нужны целиком: вернуть пустой документ — не вернуть.

        Раньше они уходили каскадом вместе с документом.
        """
        document = self.make_inbound()
        self.client.delete(f'/api/inbound-documents/{document.pk}/')
        self.assertEqual(InboundItem.objects.filter(
            inbound_doc_id=document.pk).count(), 1)

    def test_it_disappears_from_the_usual_lists(self):
        document = self.make_inbound()
        self.client.delete(f'/api/inbound-documents/{document.pk}/')
        self.assertFalse(InboundDocument.objects.filter(
            pk=document.pk).exists())

        listing = self.client.get('/api/inbound-documents/').json()
        numbers = [row['doc_number'] for row in listing['results']]
        self.assertNotIn(document.doc_number, numbers)

    def test_who_deleted_is_remembered(self):
        document = self.make_inbound()
        self.client.delete(f'/api/inbound-documents/{document.pk}/')
        document.refresh_from_db()
        self.assertEqual(document.deleted_by, self.user)
        self.assertIsNotNone(document.deleted_at)

    def test_processed_document_is_still_refused(self):
        """Проведённый не удаляется и в корзину не попадает."""
        document = self.make_inbound(processed=True)
        answer = self.client.delete(f'/api/inbound-documents/{document.pk}/')
        self.assertEqual(answer.status_code, 400)
        document.refresh_from_db()
        self.assertIsNone(document.deleted_at)


class RestoreTest(TrashTestBase):
    """Возвращение из корзины."""

    def delete_it(self, document, kind='inbound-documents'):
        self.client.delete(f'/api/{kind}/{document.pk}/')
        document.refresh_from_db()
        return document

    def test_restored_document_is_back_in_the_lists(self):
        document = self.delete_it(self.make_inbound())
        answer = self.client.post(
            f'/api/trash/inbound/{document.pk}/restore/')
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(InboundDocument.objects.filter(
            pk=document.pk).exists())

    def test_restored_document_keeps_its_lines(self):
        document = self.delete_it(self.make_inbound())
        self.client.post(f'/api/trash/inbound/{document.pk}/restore/')
        document.refresh_from_db()
        self.assertEqual(document.items.count(), 1)
        self.assertEqual(document.items.first().quantity, Decimal('10'))

    def test_taken_number_is_explained_not_crashed(self):
        """Пока документ лежал в корзине, его номер могли занять.

        Номер в системе один на всю базу. Молча упасть на уровне базы
        здесь нельзя — человеку надо сказать, что делать.
        """
        document = self.delete_it(self.make_inbound(number='ПР-7'))
        self.make_inbound(number='ПР-7')

        answer = self.client.post(
            f'/api/trash/inbound/{document.pk}/restore/')
        self.assertEqual(answer.status_code, 400)
        self.assertIn('занят', answer.json()['error'])

    def test_unknown_kind_is_refused(self):
        answer = self.client.post('/api/trash/выдумка/1/restore/')
        self.assertEqual(answer.status_code, 400)

    def test_restoring_twice_says_so(self):
        document = self.delete_it(self.make_inbound())
        self.client.post(f'/api/trash/inbound/{document.pk}/restore/')
        answer = self.client.post(
            f'/api/trash/inbound/{document.pk}/restore/')
        self.assertEqual(answer.status_code, 404)


class TrashListTest(TrashTestBase):
    """Что видно в корзине."""

    def test_both_kinds_are_listed(self):
        inbound = self.make_inbound()
        outbound = OutboundDocument.objects.create(
            doc_number='РС-1', doc_date=date.today(),
            warehouse=self.warehouse, purpose='sale', created_by=self.user)
        self.client.delete(f'/api/inbound-documents/{inbound.pk}/')
        self.client.delete(f'/api/outbound-documents/{outbound.pk}/')

        rows = self.client.get('/api/trash/').json()['results']
        self.assertEqual({row['kind'] for row in rows}, {'inbound', 'outbound'})

    def test_row_says_enough_to_recognise_the_document(self):
        document = self.make_inbound(number='ПР-42')
        self.client.delete(f'/api/inbound-documents/{document.pk}/')

        row = self.client.get('/api/trash/').json()['results'][0]
        self.assertEqual(row['doc_number'], 'ПР-42')
        self.assertEqual(row['warehouse'], 'Основной')
        self.assertEqual(row['lines'], 1)
        self.assertEqual(row['deleted_by'], 'klad')

    @override_settings(TRASH_KEEP_DAYS=30)
    def test_days_left_are_counted_down(self):
        document = self.make_inbound()
        self.client.delete(f'/api/inbound-documents/{document.pk}/')
        InboundDocument.all_objects.filter(pk=document.pk).update(
            deleted_at=timezone.now() - timedelta(days=28))

        row = items()[0]
        self.assertEqual(row['days_left'], 2)

    def test_stranger_cannot_look_into_the_trash(self):
        """Корзина — те же документы. Кто не вправе их править, тому и
        смотреть незачем."""
        self.client.logout()
        answer = self.client.get('/api/trash/')
        self.assertIn(answer.status_code, (401, 403))


class PurgeTest(TrashTestBase):
    """Очистка по сроку."""

    def aged(self, days):
        document = self.make_inbound(number=f'ПР-{days}')
        self.client.delete(f'/api/inbound-documents/{document.pk}/')
        InboundDocument.all_objects.filter(pk=document.pk).update(
            deleted_at=timezone.now() - timedelta(days=days))
        return document

    @override_settings(TRASH_KEEP_DAYS=30)
    def test_old_is_removed_for_good(self):
        old = self.aged(40)
        purge()
        self.assertFalse(InboundDocument.all_objects.filter(
            pk=old.pk).exists())

    @override_settings(TRASH_KEEP_DAYS=30)
    def test_recent_is_kept(self):
        recent = self.aged(3)
        purge()
        self.assertTrue(InboundDocument.all_objects.filter(
            pk=recent.pk).exists())

    def run_command(self, **options):
        out = StringIO()
        call_command('purgetrash', stdout=out, stderr=out, **options)
        return out.getvalue()

    def test_command_asks_before_removing(self):
        """Без согласия команда не удаляет ничего.

        Спрашивать через input нельзя: команду ставят в расписание, и
        ожидание ответа выглядит там как зависшее задание.
        """
        old = self.aged(40)
        text = self.run_command(days=30)
        self.assertIn('Ничего не удалено', text)
        self.assertTrue(InboundDocument.all_objects.filter(
            pk=old.pk).exists())

    def test_command_removes_with_consent(self):
        old = self.aged(40)
        text = self.run_command(days=30, yes=True)
        self.assertIn('Вычищено', text)
        self.assertFalse(InboundDocument.all_objects.filter(
            pk=old.pk).exists())

    def test_command_says_when_there_is_nothing_to_do(self):
        text = self.run_command(days=30)
        self.assertIn('нет документов', text)


class TrashPageTest(TrashTestBase):
    """Страница корзины."""

    def test_page_opens(self):
        page = self.client.get('/trash/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Корзина')

    def test_page_is_linked_from_every_page(self):
        page = self.client.get('/')
        self.assertContains(page, 'href="/trash/"')
