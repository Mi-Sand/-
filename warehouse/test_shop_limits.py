"""Пределы для заказов с витрины.

Витрина открыта без входа, и товар при оформлении уходит в резерв.
Вред здесь не в порче данных — данные проверяются как следует, — а в
помехе работе: сотня заказов за минуту, весь товар обещан, настоящие
покупатели видят «нет в наличии».
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Order, OrderItem, Product, Stock, Warehouse
from .order_services import (TooManyOrdersError, check_order_rate,
                             get_available_quantity)


def make_product(**kwargs):
    values = dict(article_number='МЯ-1', name='Мяч футбольный',
                  category='equipment', size='5', color='белый',
                  cost=Decimal('300'), selling_price=Decimal('1500'))
    values.update(kwargs)
    return Product.objects.create(**values)


class OrderRateTest(TestCase):
    """Сколько заказов принимаем с одного адреса и телефона."""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(
            name='Основной', type='finished')
        self.product = make_product()
        Stock.objects.create(warehouse=self.warehouse, product=self.product,
                             quantity=Decimal('100'))

    def order(self, phone='+79990000001', ip='10.0.0.1', status='new',
              when=None):
        order = Order.objects.create(
            number=f'З-{Order.objects.count() + 1000}',
            customer_name='Иванов Иван', customer_phone=phone,
            created_ip=ip, status=status)
        OrderItem.objects.create(order=order, product=self.product,
                                 quantity=Decimal('1'),
                                 price=self.product.selling_price)
        if when:
            Order.objects.filter(pk=order.pk).update(created_at=when)
        return order

    def test_a_few_orders_are_fine(self):
        """Предел не должен мешать обычному покупателю."""
        self.order()
        self.order()
        check_order_rate(source_ip='10.0.0.1', phone='+79990000001')

    def test_too_many_from_one_address(self):
        for _ in range(5):
            self.order(phone='+79990000002', ip='10.0.0.7')
        with self.assertRaises(TooManyOrdersError) as caught:
            check_order_rate(source_ip='10.0.0.7', phone='+79990000009')
        self.assertIn('позвоните', str(caught.exception).lower())

    def test_too_many_on_one_phone(self):
        """Адрес у мобильного интернета меняется сам — телефон нет."""
        for number in range(3):
            self.order(phone='+79990000003', ip=f'10.0.0.{number}')
        with self.assertRaises(TooManyOrdersError):
            check_order_rate(source_ip='10.0.0.99', phone='+79990000003')

    def test_phone_written_differently_is_the_same_phone(self):
        """Пробелы и скобки не должны обходить предел."""
        for _ in range(3):
            self.order(phone='+79990000004')
        with self.assertRaises(TooManyOrdersError):
            check_order_rate(source_ip='10.0.0.50',
                             phone='8 (999) 000-00-04')

    def test_yesterday_orders_do_not_count(self):
        """Предел на час, а не на всю жизнь покупателя."""
        long_ago = timezone.now() - timedelta(hours=3)
        for _ in range(6):
            self.order(phone='+79990000005', ip='10.0.0.8', when=long_ago)
        check_order_rate(source_ip='10.0.0.8', phone='+79990000005')

    def test_cancelled_orders_do_not_count(self):
        """Человек мог ошибиться, отменить и оформить заново."""
        for _ in range(6):
            self.order(phone='+79990000006', ip='10.0.0.9',
                       status='cancelled')
        check_order_rate(source_ip='10.0.0.9', phone='+79990000006')

    def test_order_from_staff_is_not_limited(self):
        """У заказа, заведённого сотрудником, адреса нет — и предела тоже."""
        for _ in range(9):
            self.order(phone='+79990000007', ip=None)
        check_order_rate(source_ip=None, phone='+79990000008')

    @override_settings(SHOP_ORDER_LIMITS={'per_ip_per_hour': 1,
                                          'per_phone_per_hour': 1})
    def test_limits_come_from_settings(self):
        self.order(ip='10.0.0.11', phone='+79990000010')
        with self.assertRaises(TooManyOrdersError):
            check_order_rate(source_ip='10.0.0.11', phone='+79990000012')

    @override_settings(SHOP_ORDER_LIMITS={'per_ip_per_hour': 0,
                                          'per_phone_per_hour': 0})
    def test_limits_can_be_switched_off(self):
        for _ in range(20):
            self.order(ip='10.0.0.12', phone='+79990000013')
        check_order_rate(source_ip='10.0.0.12', phone='+79990000013')


class ShopOrderApiLimitTest(TestCase):
    """Тот же предел, но через саму витрину."""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(
            name='Основной', type='finished')
        self.product = make_product()
        Stock.objects.create(warehouse=self.warehouse, product=self.product,
                             quantity=Decimal('100'))

    def send(self, phone='+7 999 111-22-33'):
        return self.client.post('/api/shop/orders/', {
            'customer_name': 'Иванов Иван',
            'customer_phone': phone,
            'address': 'Москва, ул. Мира, 1',
            'items': [{'product': self.product.id, 'quantity': 1}],
        }, content_type='application/json')

    def test_first_orders_pass(self):
        self.assertEqual(self.send().status_code, 201)

    def test_address_is_written_down(self):
        """Без записанного адреса ограничивать частоту не по чему."""
        self.send()
        order = Order.objects.latest('id')
        self.assertTrue(order.created_ip)

    def test_limit_answers_429(self):
        """Отдельный код ответа: по журналу видно, что дело в частоте.

        Ответ 400 смешался бы с ошибками в данных, и разобраться, что
        происходит на витрине, было бы нечем.
        """
        for number in range(5):
            self.send(phone=f'+7 999 111-22-{30 + number}')
        response = self.send(phone='+7 999 111-22-99')
        self.assertEqual(response.status_code, 429)
        self.assertIn('error', response.json())

    def test_forwarded_header_is_not_trusted_by_default(self):
        """Иначе предел обходится одной строкой в запросе.

        Заголовок X-Forwarded-For подделывается кем угодно. Доверять
        ему можно только когда впереди стоит nginx, и это включается
        настройкой отдельно.
        """
        for number in range(5):
            self.client.post(
                '/api/shop/orders/', {
                    'customer_name': 'Иванов Иван',
                    'customer_phone': f'+7 999 222-33-{40 + number}',
                    'address': 'Москва, ул. Мира, 1',
                    'items': [{'product': self.product.id, 'quantity': 1}],
                }, content_type='application/json',
                HTTP_X_FORWARDED_FOR=f'203.0.113.{number}')
        response = self.client.post(
            '/api/shop/orders/', {
                'customer_name': 'Иванов Иван',
                'customer_phone': '+7 999 222-33-99',
                'address': 'Москва, ул. Мира, 1',
                'items': [{'product': self.product.id, 'quantity': 1}],
            }, content_type='application/json',
            HTTP_X_FORWARDED_FOR='203.0.113.250')
        self.assertEqual(response.status_code, 429)


class ExpireOrdersTest(TestCase):
    """Снятие резерва с заказов, которых никто не подтвердил."""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(
            name='Основной', type='finished')
        self.product = make_product()
        Stock.objects.create(warehouse=self.warehouse, product=self.product,
                             quantity=Decimal('10'))

    def order(self, hours_ago=0, status='new', quantity=Decimal('4')):
        order = Order.objects.create(
            number=f'З-{Order.objects.count() + 500}',
            customer_name='Петров Пётр', customer_phone='+79990000000',
            status=status)
        OrderItem.objects.create(order=order, product=self.product,
                                 quantity=quantity,
                                 price=self.product.selling_price)
        if hours_ago:
            Order.objects.filter(pk=order.pk).update(
                created_at=timezone.now() - timedelta(hours=hours_ago))
        return order

    def run_command(self, **options):
        out = StringIO()
        call_command('expireorders', stdout=out, stderr=out, **options)
        return out.getvalue()

    def test_stale_order_is_cancelled(self):
        order = self.order(hours_ago=30)
        self.run_command()
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')

    def test_goods_return_to_the_shop_window(self):
        """Ради этого всё и делается: товар снова виден покупателям."""
        self.order(hours_ago=30, quantity=Decimal('10'))
        self.assertEqual(get_available_quantity(self.product.id), 0)
        self.run_command()
        self.assertEqual(get_available_quantity(self.product.id), 10)

    def test_fresh_order_is_left_alone(self):
        order = self.order(hours_ago=2)
        self.run_command()
        order.refresh_from_db()
        self.assertEqual(order.status, 'new')

    def test_confirmed_order_is_never_touched(self):
        """За подтверждённым стоит договорённость с покупателем."""
        order = self.order(hours_ago=300, status='confirmed')
        self.run_command()
        order.refresh_from_db()
        self.assertEqual(order.status, 'confirmed')

    def test_reason_is_written_in_the_order(self):
        """Через месяц никто не вспомнит, почему заказ отменён."""
        order = self.order(hours_ago=30)
        self.run_command()
        order.refresh_from_db()
        self.assertIn('не подтверждён', order.comment)

    def test_customer_comment_is_kept(self):
        order = self.order(hours_ago=30)
        # Через queryset, а не order.save(): обычное сохранение записало
        # бы вместе с полем и нынешнюю дату создания, заказ перестал бы
        # быть просроченным, и проверка ничего бы не проверила.
        Order.objects.filter(pk=order.pk).update(
            comment='Позвонить после 18:00')
        self.run_command()
        order.refresh_from_db()
        self.assertIn('Позвонить после 18:00', order.comment)
        self.assertIn('Отменён системой', order.comment)

    def test_dry_run_changes_nothing(self):
        order = self.order(hours_ago=30)
        output = self.run_command(dry_run=True)
        order.refresh_from_db()
        self.assertEqual(order.status, 'new')
        self.assertIn('вхолостую', output)

    def test_hours_can_be_given(self):
        order = self.order(hours_ago=3)
        self.run_command(hours=2)
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')

    @override_settings(SHOP_ORDER_LIMITS={'unconfirmed_hours': 0})
    def test_zero_hours_switches_it_off(self):
        order = self.order(hours_ago=300)
        output = self.run_command()
        order.refresh_from_db()
        self.assertEqual(order.status, 'new')
        self.assertIn('отключён', output)

    def test_quiet_says_nothing_when_there_is_nothing(self):
        self.assertEqual(self.run_command(quiet=True).strip(), '')
