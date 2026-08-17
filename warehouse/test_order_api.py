"""Заведение заказа из закрытой части.

Покупатель звонит по телефону — менеджер заводит заказ сам. Раньше на
этом пути заказ сохранялся как обычная запись: без номера, без позиций
и без проверки наличия. В списке появлялась карточка «Итого: 0», по
которой нельзя ни отгрузить, ни понять, что заказано. Нашлось это
перебором негодных данных по API.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Order, Product, Stock, Warehouse

User = get_user_model()


class StaffOrderTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='menedzher', password='x', role='manager')
        self.client.force_login(self.user)
        self.warehouse = Warehouse.objects.create(name='Готовая',
                                                  type='finished')
        self.product = Product.objects.create(
            article_number='МЯ-1', name='Мяч футбольный', category='equipment',
            size='5', color='белый', cost=Decimal('300'),
            selling_price=Decimal('1500'))
        Stock.objects.create(warehouse=self.warehouse, product=self.product,
                             quantity=Decimal('10'))

    def create(self, **fields):
        data = {'customer_name': 'Иванов Иван', 'customer_phone': '+79990000000',
                'items': [{'product': self.product.pk, 'quantity': 2}]}
        data.update(fields)
        return self.client.post('/api/orders/', data,
                                content_type='application/json')

    def test_order_gets_a_number(self):
        answer = self.create()
        self.assertEqual(answer.status_code, 201)
        self.assertTrue(answer.json()['number'].startswith('ЗАК-'))

    def test_items_are_saved(self):
        answer = self.create()
        order = Order.objects.get(pk=answer.json()['id'])
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.total, Decimal('3000'))

    def test_order_without_items_is_refused(self):
        """Пустой заказ ни отгрузить, ни понять."""
        answer = self.create(items=[])
        self.assertEqual(answer.status_code, 400)
        self.assertIn('позиции', answer.json()['error'])
        self.assertEqual(Order.objects.count(), 0)

    def test_nonsense_in_items_is_refused(self):
        for items in ('нет', 42, [{'product': 'абв', 'quantity': 1}],
                      [{'product': self.product.pk, 'quantity': 'много'}]):
            answer = self.create(items=items)
            self.assertEqual(answer.status_code, 400, items)
        self.assertEqual(Order.objects.count(), 0)

    def test_more_than_in_stock_is_refused(self):
        """Та же проверка наличия, что и на витрине."""
        answer = self.create(items=[{'product': self.product.pk,
                                     'quantity': 100}])
        self.assertEqual(answer.status_code, 409)
        self.assertIn('доступно', answer.json()['error'])
        self.assertEqual(Order.objects.count(), 0)

    def test_stranger_cannot_create(self):
        self.client.logout()
        self.assertIn(self.create().status_code, (401, 403))
