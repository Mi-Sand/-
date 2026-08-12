"""Число запросов к базе не должно расти вместе с числом записей.

Беда эта тихая: на десятке документов всё быстро, на тысяче страница
открывается полминуты, и виновата не «медленная база», а лишний запрос
на каждую строку. Заметить её глазами нельзя — только счётом.

Проверка ловила настоящее четырежды: список продукции (после
добавления фотографий), приход и расход (названия позиций в строках) и
корзину (число строк считалось по одному документу за раз).
"""
from decimal import Decimal
from datetime import date
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from warehouse.models import (InboundDocument, InboundItem, Material,
                              Order, OrderItem, OutboundDocument,
                              OutboundItem, Product, ProductPhoto,
                              Stock, Supplier, Warehouse)

class NPlusOneTest(TestCase):
    """Списки: три записи и сорок должны стоить одинаково."""

    def setUp(self):
        self.u = get_user_model().objects.create_user(username='q', password='x', role='admin')
        self.client.force_login(self.u)
        self.w = Warehouse.objects.create(name='Осн', type='raw')
        self.s = Supplier.objects.create(name='П')
        self.m = Material.objects.create(name='М', unit='kg', category='textile', reorder_point=Decimal('1'))

    def grow(self, make, url):
        def count(n):
            make(n)
            with CaptureQueriesContext(connection) as q:
                r = self.client.get(url)
            assert r.status_code == 200, (url, r.status_code)
            return len(q.captured_queries)
        small = count(3); big = count(40)
        print(f'\n  {url}: 3 записи -> {small} запросов, 40 -> {big}')
        self.assertLessEqual(big, small + 3, f'{url}: запросы растут с числом записей')

    def test_products(self):
        def make(n):
            Product.objects.all().delete()
            for i in range(n):
                p = Product.objects.create(article_number=f'A{i}', name=f'Т{i}', category='shoes',
                    size='40', color='ч', cost=Decimal('1'), selling_price=Decimal('2'))
                ProductPhoto.objects.create(product=p, image='x.jpg')
        self.grow(make, '/api/products/?limit=200')

    def test_inbound(self):
        def make(n):
            InboundDocument.all_objects.all().delete()
            for i in range(n):
                d = InboundDocument.objects.create(doc_number=f'ПР-{i}', doc_date=date.today(),
                    supplier=self.s, warehouse=self.w, created_by=self.u)
                InboundItem.objects.create(inbound_doc=d, material=self.m,
                    quantity=Decimal('1'), unit_price=Decimal('1'))
        self.grow(make, '/api/inbound-documents/?limit=200')

    def test_outbound(self):
        def make(n):
            OutboundDocument.all_objects.all().delete()
            for i in range(n):
                d = OutboundDocument.objects.create(doc_number=f'РС-{i}', doc_date=date.today(),
                    warehouse=self.w, purpose='sale', created_by=self.u)
                OutboundItem.objects.create(outbound_doc=d, material=self.m,
                    quantity=Decimal('1'), unit_price=Decimal('1'))
        self.grow(make, '/api/outbound-documents/?limit=200')

    def test_orders(self):
        def make(n):
            Order.objects.all().delete()
            p = Product.objects.create(article_number='Z', name='Z', category='shoes', size='1',
                color='ч', cost=Decimal('1'), selling_price=Decimal('2')) if not Product.objects.exists() else Product.objects.first()
            for i in range(n):
                o = Order.objects.create(number=f'З-{i}', customer_name='И', customer_phone='+7')
                OrderItem.objects.create(order=o, product=p, quantity=1, price=Decimal('2'))
        self.grow(make, '/api/orders/?limit=200')

    def test_trash(self):
        def make(n):
            InboundDocument.all_objects.all().delete()
            from django.utils import timezone
            for i in range(n):
                d = InboundDocument.objects.create(doc_number=f'ПР-{i}', doc_date=date.today(),
                    supplier=self.s, warehouse=self.w, created_by=self.u)
                InboundItem.objects.create(inbound_doc=d, material=self.m,
                    quantity=Decimal('1'), unit_price=Decimal('1'))
                InboundDocument.all_objects.filter(pk=d.pk).update(deleted_at=timezone.now(), deleted_by=self.u)
        self.grow(make, '/api/trash/')

    def test_shop(self):
        def make(n):
            Product.objects.all().delete()
            for i in range(n):
                p = Product.objects.create(article_number=f'S{i}', name=f'Т{i}', category='shoes',
                    size='40', color='ч', cost=Decimal('1'), selling_price=Decimal('2'))
                ProductPhoto.objects.create(product=p, image='x.jpg')
                Stock.objects.create(warehouse=self.w, product=p, quantity=Decimal('5'))
        self.grow(make, '/api/shop/products/')

    def test_audit(self):
        def make(n):
            for i in range(n):
                Material.objects.create(name=f'М{i}', unit='kg', category='textile', reorder_point=Decimal('1'))
        self.grow(make, '/api/audit/?limit=200')
