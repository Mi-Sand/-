"""Проверки, закрывающие ошибки, найденные при сплошном обходе программы.

Здесь две группы.

1. Корзина с витрины. Заказ оформляет посторонний человек без входа в
   систему, поэтому в теле запроса может прийти что угодно. Нечисловое
   количество раньше роняло сервер отказом 500 вместо понятного ответа.

2. Удаление справочников. Остатки и движения связаны со справочником
   каскадом: удаление материала уносило с собой и остаток, и всю историю
   приходов и расходов — по одному нажатию, без возможности вернуть.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from warehouse.models import (Material, Product, Stock, StockMovement,
                              Warehouse)
from warehouse.order_services import create_order

User = get_user_model()


class ShopCartInputTest(TestCase):
    """Корзина приходит от постороннего — принимать её на веру нельзя."""

    @classmethod
    def setUpTestData(cls):
        cls.warehouse = Warehouse.objects.create(name='Готовая', type='finished')
        cls.product = Product.objects.create(
            article_number='МАК-01', name='Макивара', category='equipment',
            size='L', color='синий', cost=500, selling_price=1300,
            status='active')
        Stock.objects.create(warehouse=cls.warehouse, product=cls.product,
                             quantity=10)

    def order(self, items):
        return create_order(customer_name='Иван Петров',
                            customer_phone='+7 999 123-45-67',
                            items=items)

    def test_valid_order_still_works(self):
        order = self.order([{'product': self.product.id, 'quantity': 2}])
        self.assertEqual(order.items.count(), 1)

    def test_text_quantity_is_refused_politely(self):
        """Раньше на этом сервер отдавал 500 вместо сообщения."""
        with self.assertRaises(ValueError) as caught:
            self.order([{'product': self.product.id, 'quantity': 'абв'}])
        self.assertIn('количество', str(caught.exception).lower())

    def test_infinity_is_refused(self):
        with self.assertRaises(ValueError):
            self.order([{'product': self.product.id, 'quantity': 'Infinity'}])

    def test_nan_is_refused(self):
        """NaN опаснее прочего: сравнение с нулём для него всегда ложно,
        поэтому проверку «количество больше нуля» он проходил насквозь."""
        with self.assertRaises(ValueError):
            self.order([{'product': self.product.id, 'quantity': 'NaN'}])

    def test_missing_fields_are_refused(self):
        for items in ([{'product': self.product.id}],
                      [{'quantity': 1}],
                      [{'product': None, 'quantity': 1}],
                      [{'product': self.product.id, 'quantity': None}]):
            with self.subTest(items=items):
                with self.assertRaises(ValueError):
                    self.order(items)

    def test_wrong_shape_is_refused(self):
        for items in ('строка', [1, 2, 3], [['пара']], {'product': 1}):
            with self.subTest(items=items):
                with self.assertRaises(ValueError):
                    self.order(items)

    def test_zero_and_negative_refused(self):
        for qty in (0, -1, '-0.5'):
            with self.subTest(qty=qty):
                with self.assertRaises(ValueError):
                    self.order([{'product': self.product.id, 'quantity': qty}])

    def test_same_product_twice_is_summed(self):
        """Две строки одного товара считаются вместе, а не по отдельности."""
        with self.assertRaises(Exception):
            self.order([{'product': self.product.id, 'quantity': 6},
                        {'product': self.product.id, 'quantity': 6}])
        order = self.order([{'product': self.product.id, 'quantity': 4},
                            {'product': self.product.id, 'quantity': 4}])
        self.assertEqual(order.items.first().quantity, Decimal('8'))


class CatalogDeleteGuardTest(TestCase):
    """Справочник с историей удалять нельзя — иначе история пропадёт."""

    def setUp(self):
        self.user = User.objects.create_user(
            'kladovshchik', password='x', is_superuser=True)
        self.client.force_login(self.user)
        self.warehouse = Warehouse.objects.create(name='Сырьё', type='raw')
        self.material = Material.objects.create(
            name='Кожа', unit='m', category='leather', reorder_point=10)
        self.product = Product.objects.create(
            article_number='МАК-01', name='Макивара', category='equipment',
            size='L', color='синий', cost=500, selling_price=1300,
            status='active')

    # --- материал --------------------------------------------------------
    def test_material_with_stock_is_protected(self):
        Stock.objects.create(warehouse=self.warehouse,
                             material=self.material, quantity=500)
        response = self.client.delete(f'/api/materials/{self.material.id}/')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Material.objects.filter(pk=self.material.pk).exists())

    def test_material_with_history_is_protected(self):
        """Даже при нулевом остатке история движений должна остаться."""
        Stock.objects.create(warehouse=self.warehouse,
                             material=self.material, quantity=0)
        StockMovement.objects.create(warehouse=self.warehouse,
                                     material=self.material,
                                     movement_type='in', quantity=100)
        response = self.client.delete(f'/api/materials/{self.material.id}/')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Material.objects.filter(pk=self.material.pk).exists())

    def test_unused_material_still_deletable(self):
        """Заведённую по ошибке позицию убрать по-прежнему можно."""
        response = self.client.delete(f'/api/materials/{self.material.id}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Material.objects.filter(pk=self.material.pk).exists())

    def test_refusal_says_what_to_do(self):
        Stock.objects.create(warehouse=self.warehouse,
                             material=self.material, quantity=5)
        response = self.client.delete(f'/api/materials/{self.material.id}/')
        self.assertIn('спишите', response.json()['error'].lower())

    # --- продукция -------------------------------------------------------
    def test_product_with_stock_is_protected(self):
        Stock.objects.create(warehouse=self.warehouse,
                             product=self.product, quantity=3)
        response = self.client.delete(f'/api/products/{self.product.id}/')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_product_refusal_points_to_archiving(self):
        Stock.objects.create(warehouse=self.warehouse,
                             product=self.product, quantity=3)
        response = self.client.delete(f'/api/products/{self.product.id}/')
        self.assertIn('снят с производства',
                      response.json()['error'].lower())

    # --- склад -----------------------------------------------------------
    def test_warehouse_with_stock_is_protected(self):
        Stock.objects.create(warehouse=self.warehouse,
                             material=self.material, quantity=7)
        response = self.client.delete(f'/api/warehouses/{self.warehouse.id}/')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Warehouse.objects.filter(pk=self.warehouse.pk).exists())

    def test_empty_warehouse_still_deletable(self):
        response = self.client.delete(f'/api/warehouses/{self.warehouse.id}/')
        self.assertEqual(response.status_code, 204)

    # --- главное: история не должна пропадать ----------------------------
    def test_history_survives_delete_attempt(self):
        """Смысл всей защиты: после отказа история на месте."""
        Stock.objects.create(warehouse=self.warehouse,
                             material=self.material, quantity=500)
        for _ in range(3):
            StockMovement.objects.create(warehouse=self.warehouse,
                                         material=self.material,
                                         movement_type='in', quantity=100)
        self.client.delete(f'/api/materials/{self.material.id}/')
        self.assertEqual(
            StockMovement.objects.filter(material=self.material).count(), 3)
        self.assertEqual(
            Stock.objects.filter(material=self.material).count(), 1)


class ReportParameterTest(TestCase):
    """Параметры отчётов приходят из адресной строки — там бывает всё.

    Отчёт о движении переводил «дни» в число напрямую: адрес вида
    ?days=abc отдавал отказ сервера. Номер склада уходил в отбор как
    есть, и база отвечала «Field 'id' expected a number».
    """

    def setUp(self):
        self.user = User.objects.create_user(
            'menedzher', password='x', is_superuser=True)
        self.client.force_login(self.user)

    BAD = ['abc', '', '-5', '1e999', '99999999999999999999', '1.5', '<b>']

    def test_days_never_crashes(self):
        for endpoint in ('/api/reports/movement/',
                         '/api/reports/production-cost/'):
            for value in self.BAD:
                with self.subTest(endpoint=endpoint, days=value):
                    response = self.client.get(f'{endpoint}?days={value}')
                    self.assertLess(response.status_code, 500)

    def test_warehouse_id_never_crashes(self):
        for endpoint in ('/api/reports/stock/', '/api/reports/movement/',
                         '/api/reports/stock/export/'):
            for value in self.BAD:
                with self.subTest(endpoint=endpoint, warehouse=value):
                    response = self.client.get(f'{endpoint}?warehouse={value}')
                    self.assertLess(response.status_code, 500)

    def test_inventory_id_never_crashes(self):
        for value in self.BAD:
            with self.subTest(inventory=value):
                response = self.client.get(
                    f'/api/reports/inventory/?inventory={value}')
                self.assertLess(response.status_code, 500)

    def test_good_parameters_still_work(self):
        self.assertEqual(
            self.client.get('/api/reports/movement/?days=7').status_code, 200)
        self.assertEqual(
            self.client.get('/api/reports/stock/').status_code, 200)

    def test_huge_period_is_clamped_not_refused(self):
        """Большой период не должен валить перевод в число."""
        response = self.client.get('/api/reports/movement/?days=100000')
        self.assertEqual(response.status_code, 200)
