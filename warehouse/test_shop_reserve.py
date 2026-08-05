"""Витрина должна показывать наличие с учётом резерва."""
from rest_framework.test import APITestCase

from warehouse.models import Product, Stock, Warehouse
from warehouse.order_services import create_order


class ShopAvailabilityTest(APITestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(name='Склад', type='finished')
        self.p = Product.objects.create(
            article_number='C-1', name='Мяч', category='equipment',
            size='5', color='синий', cost=300, selling_price=1800)
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=10)

    def _shop_stock(self):
        r = self.client.get('/api/shop/products/')
        variant = r.data['products'][0]['colors'][0]['variants'][0]
        return variant['in_stock'], variant['available']

    def test_shows_full_stock_without_orders(self):
        qty, available = self._shop_stock()
        self.assertEqual(qty, 10)
        self.assertTrue(available)

    def test_reserved_reduces_shown_stock(self):
        """После заказа витрина показывает меньше — часть зарезервирована."""
        create_order('Иван Петров', '+79991234567', [{'product': self.p.id, 'quantity': 4}])
        qty, available = self._shop_stock()
        self.assertEqual(qty, 6)
        self.assertTrue(available)

    def test_fully_reserved_shows_out_of_stock(self):
        """Когда всё зарезервировано — товар показан как отсутствующий."""
        create_order('Иван Петров', '+79991234567', [{'product': self.p.id, 'quantity': 10}])
        qty, available = self._shop_stock()
        self.assertEqual(qty, 0)
        self.assertFalse(available)
