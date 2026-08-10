"""Проверка отбора документов.

Кладовщик помнит не номер документа, а поставщика, товар или неделю,
когда это было. Поиск только по номеру означал, что искать приходится
глазами по всему списку.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (InboundDocument, InboundItem, Material, OutboundDocument,
                     OutboundItem, Product, Supplier, Warehouse)

User = get_user_model()


class InboundSearchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='klad', password='x', role='storekeeper')
        self.client.force_login(self.user)

        self.warehouse = Warehouse.objects.create(name='Основной', type='raw')
        self.leather = Material.objects.create(
            name='Кожа хромовая', unit='m', category='leather',
            reorder_point=Decimal('5'))
        self.thread = Material.objects.create(
            name='Нитки капроновые', unit='m', category='textile',
            reorder_point=Decimal('5'))

        self.rostov = Supplier.objects.create(name='ООО Ростов-КОЖ-Сырьё')
        self.other = Supplier.objects.create(name='ООО Швейснаб')

        self.first = self.make('ПР-1', date(2026, 1, 10), self.rostov,
                               self.leather)
        self.second = self.make('ПР-2', date(2026, 5, 20), self.other,
                                self.thread)

    def make(self, number, when, supplier, material):
        document = InboundDocument.objects.create(
            doc_number=number, doc_date=when, supplier=supplier,
            warehouse=self.warehouse, created_by=self.user)
        InboundItem.objects.create(
            inbound_doc=document, material=material,
            quantity=Decimal('10'), unit_price=Decimal('100'))
        return document

    def numbers(self, query=''):
        response = self.client.get(f'/api/inbound-documents/{query}')
        self.assertEqual(response.status_code, 200)
        return sorted(row['doc_number'] for row in response.json()['results'])

    def test_without_search_everything_is_there(self):
        self.assertEqual(self.numbers(), ['ПР-1', 'ПР-2'])

    def test_by_number(self):
        self.assertEqual(self.numbers('?search=ПР-2'), ['ПР-2'])

    def test_by_supplier(self):
        """Главное новое: искать по тому, что помнят."""
        self.assertEqual(self.numbers('?search=Ростов'), ['ПР-1'])

    def test_by_goods(self):
        self.assertEqual(self.numbers('?search=Нитки'), ['ПР-2'])

    def test_search_ignores_case(self):
        self.assertEqual(self.numbers('?search=кожа'), ['ПР-1'])

    def test_document_is_not_shown_twice(self):
        """Документ с двумя подходящими строками — всё равно один.

        Поиск идёт и по строкам документа, а строк несколько: без
        distinct он показался бы дважды, и в списке появились бы
        призраки.
        """
        InboundItem.objects.create(
            inbound_doc=self.first, material=self.thread,
            quantity=Decimal('1'), unit_price=Decimal('1'))
        first = self.make('ПР-3', date(2026, 2, 2), self.rostov, self.leather)
        InboundItem.objects.create(
            inbound_doc=first, material=self.leather,
            quantity=Decimal('2'), unit_price=Decimal('2'))
        response = self.client.get('/api/inbound-documents/?search=Кожа')
        rows = response.json()['results']
        self.assertEqual(len(rows), len({row['id'] for row in rows}))

    def test_by_date_range(self):
        self.assertEqual(self.numbers('?since=2026-05-01'), ['ПР-2'])
        self.assertEqual(self.numbers('?until=2026-01-31'), ['ПР-1'])
        self.assertEqual(
            self.numbers('?since=2026-01-01&until=2026-12-31'),
            ['ПР-1', 'ПР-2'])

    def test_search_and_status_work_together(self):
        self.first.processed = True
        self.first.save()
        self.assertEqual(self.numbers('?search=ООО&processed=true'), ['ПР-1'])

    def test_nothing_found_is_an_empty_list(self):
        self.assertEqual(self.numbers('?search=такого нет'), [])

    def test_broken_filters_do_not_break_the_list(self):
        """Мусор в отборе — пустой отбор, а не отказ сервера.

        Строка с нулевым байтом — особый случай: её отклоняет сам
        Django, не доходя до обработчика. Ответ 400 здесь правильный:
        такого запроса не бывает от браузера.
        """
        for query in ('?since=вчера', '?until=2020-13-45', '?since=',
                      '?until=99999999-01-01', '?search=' + 'я' * 500):
            response = self.client.get(f'/api/inbound-documents/{query}')
            self.assertEqual(response.status_code, 200, query)

        nul = self.client.get('/api/inbound-documents/?search=%00')
        self.assertEqual(nul.status_code, 400)


class OutboundSearchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='klad2', password='x', role='storekeeper')
        self.client.force_login(self.user)
        self.warehouse = Warehouse.objects.create(name='Основной', type='raw')
        self.product = Product.objects.create(
            article_number='МЯ-1', name='Мяч футбольный', category='equipment',
            size='5', color='белый', cost=Decimal('300'),
            selling_price=Decimal('1500'))

        self.first = OutboundDocument.objects.create(
            doc_number='РС-1', doc_date=date(2026, 1, 10),
            warehouse=self.warehouse, purpose='production',
            production_order='ЗП-77', created_by=self.user)
        OutboundItem.objects.create(
            outbound_doc=self.first, product=self.product,
            quantity=Decimal('2'), unit_price=Decimal('1500'))

        self.second = OutboundDocument.objects.create(
            doc_number='РС-2', doc_date=date(2026, 6, 1),
            warehouse=self.warehouse, purpose='sale', created_by=self.user)

    def numbers(self, query=''):
        response = self.client.get(f'/api/outbound-documents/{query}')
        self.assertEqual(response.status_code, 200)
        return sorted(row['doc_number'] for row in response.json()['results'])

    def test_by_production_order(self):
        """По расходу ищут производственный заказ: куда ушёл материал."""
        self.assertEqual(self.numbers('?search=ЗП-77'), ['РС-1'])

    def test_by_goods(self):
        self.assertEqual(self.numbers('?search=Мяч'), ['РС-1'])

    def test_by_date(self):
        self.assertEqual(self.numbers('?since=2026-05-01'), ['РС-2'])


class CatalogSearchTest(TestCase):
    """Тот же поиск в справочниках.

    Строчными буквами набирают чаще, чем с большой: если «кожа» не
    находит «Кожа хромовая», человек решает, что материала в системе
    нет, и заводит второй такой же.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='ekonomist', password='x', role='economist')
        self.client.force_login(self.user)
        Material.objects.create(
            name='Кожа хромовая', unit='m', category='leather',
            article_number='КЖ-1', reorder_point=Decimal('5'))
        Product.objects.create(
            article_number='МЯ-1', name='Мяч футбольный', category='equipment',
            size='5', color='Белый', cost=Decimal('300'),
            selling_price=Decimal('1500'))
        Supplier.objects.create(name='ООО Ростов-КОЖ-Сырьё', inn='6141023001')

    def found(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.json()['count']

    def test_material_by_lowercase(self):
        self.assertEqual(self.found('/api/materials/?search=кожа'), 1)

    def test_material_by_uppercase(self):
        self.assertEqual(self.found('/api/materials/?search=КОЖА'), 1)

    def test_material_by_article(self):
        self.assertEqual(self.found('/api/materials/?search=КЖ-1'), 1)

    def test_product_by_lowercase(self):
        self.assertEqual(self.found('/api/products/?search=мяч'), 1)

    def test_product_by_colour(self):
        self.assertEqual(self.found('/api/products/?search=белый'), 1)

    def test_supplier_by_lowercase(self):
        self.assertEqual(self.found('/api/suppliers/?search=ростов'), 1)

    def test_supplier_by_inn(self):
        self.assertEqual(self.found('/api/suppliers/?search=6141023001'), 1)

    def test_nothing_found(self):
        self.assertEqual(self.found('/api/materials/?search=резина'), 0)
