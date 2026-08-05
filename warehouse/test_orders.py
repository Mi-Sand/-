"""Тесты заказов интернет-магазина: резервирование, отгрузка, отмена."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from warehouse.models import Order, Product, Stock, Warehouse
from warehouse.order_services import (cancel_order, confirm_order,
                                      create_order,
                                      get_available_quantity, ship_order)
from warehouse.services import InsufficientStockError

User = get_user_model()


class OrderReservationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('s', password='x')
        self.wh = Warehouse.objects.create(name='Склад', type='finished')
        self.product = Product.objects.create(
            article_number='A-1', name='Перчатки', category='equipment',
            size='10', color='красный', cost=400, selling_price=1500)
        Stock.objects.create(
            warehouse=self.wh, product=self.product, quantity=10)

    def test_order_reserves_not_writes_off(self):
        """Заказ резервирует товар, но не списывает со склада."""
        create_order('Иван', '+79990000000',
                     [{'product': self.product.id, 'quantity': 3}])

        # Физический остаток не изменился
        stock = Stock.objects.get(warehouse=self.wh, product=self.product)
        self.assertEqual(stock.quantity, 10)
        # Но доступно к заказу стало меньше
        self.assertEqual(get_available_quantity(self.product.id), Decimal('7'))

    def test_cannot_order_more_than_available(self):
        """Нельзя заказать больше, чем доступно."""
        with self.assertRaises(InsufficientStockError):
            create_order('Иван', '+79990000000',
                         [{'product': self.product.id, 'quantity': 11}])

    def test_reservation_blocks_second_order(self):
        """Резерв первого заказа мешает второму забрать тот же товар."""
        create_order('Первый', '+79990000001',
                     [{'product': self.product.id, 'quantity': 8}])
        # Осталось доступно 2, просим 5 — отказ
        with self.assertRaises(InsufficientStockError):
            create_order('Второй', '+79990000002',
                         [{'product': self.product.id, 'quantity': 5}])
        # А 2 — можно
        order = create_order('Второй', '+79990000002',
                             [{'product': self.product.id, 'quantity': 2}])
        self.assertEqual(order.items.count(), 1)

    def test_cancel_releases_reservation(self):
        """Отмена заказа возвращает товар в доступные."""
        order = create_order('Иван', '+79990000000',
                             [{'product': self.product.id, 'quantity': 6}])
        self.assertEqual(get_available_quantity(self.product.id), Decimal('4'))

        cancel_order(order.id)
        self.assertEqual(get_available_quantity(self.product.id), Decimal('10'))

    def test_ship_writes_off_stock(self):
        """Отгрузка реально списывает товар со склада."""
        order = create_order('Иван', '+79990000000',
                             [{'product': self.product.id, 'quantity': 4}])
        confirm_order(order.id)
        ship_order(order.id, self.wh.id, user=self.user)

        stock = Stock.objects.get(warehouse=self.wh, product=self.product)
        self.assertEqual(stock.quantity, 6)  # 10 - 4

        order.refresh_from_db()
        self.assertEqual(order.status, 'shipped')
        self.assertIsNotNone(order.outbound_document)
        self.assertTrue(order.outbound_document.processed)

    def test_shipped_order_no_longer_reserves(self):
        """После отгрузки резерв снимается (товар уже списан)."""
        order = create_order('Иван', '+79990000000',
                             [{'product': self.product.id, 'quantity': 4}])
        ship_order(order.id, self.wh.id, user=self.user)
        # Остаток 6, резерва нет → доступно 6
        self.assertEqual(get_available_quantity(self.product.id), Decimal('6'))

    def test_cannot_cancel_shipped(self):
        """Отгруженный заказ отменить нельзя."""
        order = create_order('Иван', '+79990000000',
                             [{'product': self.product.id, 'quantity': 2}])
        ship_order(order.id, self.wh.id, user=self.user)
        with self.assertRaises(ValueError):
            cancel_order(order.id)

    def test_order_requires_contacts(self):
        """Имя и телефон обязательны."""
        with self.assertRaises(ValueError):
            create_order('', '+79990000000',
                         [{'product': self.product.id, 'quantity': 1}])
        with self.assertRaises(ValueError):
            create_order('Иван', '',
                         [{'product': self.product.id, 'quantity': 1}])

    def test_price_fixed_at_order_time(self):
        """Цена в заказе фиксируется и не меняется при смене цены товара."""
        order = create_order('Иван', '+79990000000',
                             [{'product': self.product.id, 'quantity': 1}])
        self.product.selling_price = Decimal('9999')
        self.product.save()

        item = order.items.first()
        self.assertEqual(item.price, Decimal('1500.00'))


class ShopOrderAPITest(APITestCase):
    """Публичный API оформления заказа — без авторизации."""

    def setUp(self):
        self.wh = Warehouse.objects.create(name='Склад', type='finished')
        self.product = Product.objects.create(
            article_number='B-1', name='Мяч', category='equipment',
            size='5', color='белый', cost=300, selling_price=1800)
        Stock.objects.create(
            warehouse=self.wh, product=self.product, quantity=5)

    def test_public_can_create_order(self):
        """Покупатель без входа в систему может оформить заказ."""
        r = self.client.post('/api/shop/orders/', {
            'customer_name': 'Пётр',
            'customer_phone': '+79995554433',
            'customer_email': 'p@example.com',
            'address': 'г. Дмитров, ул. Ленина, д. 1',
            'comment': 'Позвонить вечером',
            'items': [{'product': self.product.id, 'quantity': 2}]
        }, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertIn('order_number', r.data)
        self.assertEqual(float(r.data['total']), 3600.0)

        order = Order.objects.get(number=r.data['order_number'])
        self.assertEqual(order.customer_name, 'Пётр')
        self.assertEqual(order.status, 'new')

    def test_server_rejects_overorder(self):
        """Сервер не верит браузеру: заказ сверх остатка отклоняется."""
        r = self.client.post('/api/shop/orders/', {
            'customer_name': 'Пётр',
            'customer_phone': '+79995554433',
            'items': [{'product': self.product.id, 'quantity': 99}]
        }, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('error', r.data)
        self.assertEqual(Order.objects.count(), 0)

    def test_orders_list_requires_auth(self):
        """Список заказов сотрудников закрыт от посторонних."""
        r = self.client.get('/api/orders/')
        self.assertIn(r.status_code, (401, 403))


class AddressValidationTest(TestCase):
    """Проверка адреса: доставка только по России."""

    def setUp(self):
        self.wh = Warehouse.objects.create(name='Склад', type='finished')
        self.p = Product.objects.create(
            article_number='D-1', name='Товар', category='equipment',
            size='M', color='синий', cost=100, selling_price=500)
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=10)

    def _order(self, address):
        return create_order(
            'Иван', '+79990000000',
            [{'product': self.p.id, 'quantity': 1}], address=address)

    def test_valid_russian_addresses(self):
        """Нормальные российские адреса принимаются."""
        good = [
            'г. Дмитров, ул. Советская, д. 10, кв. 5',
            'Московская область, г. Дубна, ул. Мира, 15',
            '141800, Дмитров, Загорская улица, дом 3',
            'Санкт-Петербург, Невский проспект, 28',
            'респ. Татарстан, г. Казань, ул. Баумана, д. 1',
        ]
        for addr in good:
            with self.subTest(addr=addr):
                order = self._order(addr)
                self.assertEqual(order.address, addr)
                order.delete()

    def test_foreign_country_rejected(self):
        """Адрес с указанием другой страны отклоняется."""
        bad = [
            'Украина, Киев, ул. Крещатик, 1',
            'Казахстан, Алматы, пр. Абая, 10',
            'Germany, Berlin, Alexanderplatz 5',
            'Беларусь, Минск, пр. Независимости, 20',
        ]
        for addr in bad:
            with self.subTest(addr=addr):
                with self.assertRaises(ValueError):
                    self._order(addr)

    def test_latin_only_rejected(self):
        """Адрес без кириллицы отклоняется."""
        with self.assertRaises(ValueError):
            self._order('Lenina street 15, apt 3')

    def test_too_short_rejected(self):
        """Слишком короткий адрес отклоняется."""
        with self.assertRaises(ValueError):
            self._order('Москва')

    def test_empty_address_allowed(self):
        """Пустой адрес допустим — самовывоз."""
        order = self._order('')
        self.assertEqual(order.address, '')

    def test_street_name_not_confused_with_country(self):
        """Названия улиц не путаются с названиями стран."""
        # «Индийская» содержит «Индия», но это улица, а не страна
        order = self._order('г. Москва, Индийская улица, д. 7')
        self.assertIn('Индийская', order.address)


class ContactValidationTest(TestCase):
    """Проверка контактов покупателя: имя и телефон."""

    def setUp(self):
        self.wh = Warehouse.objects.create(name='Склад', type='finished')
        self.p = Product.objects.create(
            article_number='E-1', name='Товар', category='equipment',
            size='M', color='чёрный', cost=100, selling_price=500)
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=50)

    def _order(self, name='Иван Петров', phone='+79990000000'):
        return create_order(
            name, phone, [{'product': self.p.id, 'quantity': 1}])

    # --- Телефон ---

    def test_phone_formats_normalized(self):
        """Привычные форматы записи приводятся к единому виду."""
        cases = [
            ('+7 999 123-45-67', '+79991234567'),
            ('8(999)123-45-67', '+79991234567'),
            ('79991234567', '+79991234567'),
            ('9991234567', '+79991234567'),
            ('+7-999-123-45-67', '+79991234567'),
        ]
        for entered, expected in cases:
            with self.subTest(phone=entered):
                order = self._order(phone=entered)
                self.assertEqual(order.customer_phone, expected)
                order.delete()

    def test_bad_phones_rejected(self):
        """Некорректные номера отклоняются."""
        for phone in ['12345', 'телефон', '+380991234567',
                      '+7 099 1234567', '', '999']:
            with self.subTest(phone=phone):
                with self.assertRaises(ValueError):
                    self._order(phone=phone)

    # --- Имя ---

    def test_valid_names(self):
        """Настоящие имена принимаются, включая составные."""
        for name in ['Иван', 'Иван Петров', 'Анна-Мария',
                     'Пётр Ильич Чайковский', "О'Коннор", 'John Smith']:
            with self.subTest(name=name):
                order = self._order(name=name)
                self.assertEqual(order.customer_name, name)
                order.delete()

    def test_name_whitespace_normalized(self):
        """Лишние пробелы убираются."""
        order = self._order(name='  Иван   Петров  ')
        self.assertEqual(order.customer_name, 'Иван Петров')

    def test_gibberish_names_rejected(self):
        """Явная белиберда отклоняется."""
        bad = [
            'ааааааа',      # повтор одной буквы
            'ыыы',          # повтор
            'фыв123',       # цифры
            'test@mail',    # символы
            'ж',            # одна буква
            'ккк',          # повтор
            'бвгджз',       # нет гласных
            '',             # пусто
            '12345',        # только цифры
        ]
        for name in bad:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self._order(name=name)


class GibberishRejectionTest(TestCase):
    """Отсечение случайного набора символов во всех полях формы.

    Тесты построены на реальных примерах, которые проходили раньше.
    """

    def setUp(self):
        self.wh = Warehouse.objects.create(name='Склад', type='finished')
        self.p = Product.objects.create(
            article_number='F-1', name='Товар', category='equipment',
            size='M', color='белый', cost=100, selling_price=500)
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=50)

    def _order(self, name='Иван Петров', phone='+79991234567',
               email='', address='', comment=''):
        return create_order(
            name, phone, [{'product': self.p.id, 'quantity': 1}],
            customer_email=email, address=address, comment=comment)

    def test_real_gibberish_name_rejected(self):
        """Набор с клавиатуры в имени отклоняется."""
        for name in ['тщжоджохжэлд', 'вававпвапваыпуф', 'фывфыв',
                     'ждлоаывп', 'йцукенгш']:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self._order(name=name)

    def test_real_gibberish_address_rejected(self):
        """Набор с клавиатуры в адресе отклоняется."""
        for addr in ['вававпвапваыпуф', 'тщжоджохжэлд асдфг',
                     'йцукен гшщзхъ фывапр']:
            with self.subTest(addr=addr):
                with self.assertRaises(ValueError):
                    self._order(address=addr)

    def test_address_without_house_number_rejected(self):
        """Адрес без номера дома отклоняется."""
        with self.assertRaises(ValueError):
            self._order(address='г. Дмитров, ул. Советская')

    def test_address_without_marker_rejected(self):
        """Текст без слов «улица», «дом» и т.п. — не адрес."""
        with self.assertRaises(ValueError):
            self._order(address='Просто какой-то текст 123')

    def test_real_addresses_still_pass(self):
        """Настоящие адреса в разных форматах принимаются."""
        good = [
            'г. Дмитров, ул. Советская, д. 10, кв. 5',
            'Московская область, город Дубна, улица Мира, дом 15',
            '141800, Дмитров, Загорская улица, 3',
            'Санкт-Петербург, Невский проспект, 28, офис 12',
            'респ. Татарстан, г. Казань, ул. Баумана, д. 1, кв. 44',
            'г. Москва, Ленинградское шоссе, д. 80, корп. 2',
        ]
        for addr in good:
            with self.subTest(addr=addr):
                order = self._order(address=addr)
                self.assertEqual(order.address, addr)
                order.delete()

    def test_real_names_still_pass(self):
        """Настоящие имена, в том числе редкие, проходят."""
        good = ['Иван', 'Иван Петров', 'Анна-Мария', "О'Коннор",
                'Пётр Ильич Чайковский', 'Щербаков Александр',
                'Гульнара Хайруллина', 'John Smith']
        for name in good:
            with self.subTest(name=name):
                order = self._order(name=name)
                order.delete()

    def test_email_validated(self):
        """E-mail проверяется по формату, если указан."""
        for bad in ['не-почта', 'ivan@', '@example.com', 'ivan@mail',
                    'ivan mail.ru']:
            with self.subTest(email=bad):
                with self.assertRaises(ValueError):
                    self._order(email=bad)

        order = self._order(email='ivan@example.com')
        self.assertEqual(order.customer_email, 'ivan@example.com')

    def test_empty_email_allowed(self):
        """E-mail необязателен."""
        order = self._order(email='')
        self.assertEqual(order.customer_email, '')

    def test_comment_spam_rejected(self):
        """Комментарий из повторов одного символа отклоняется."""
        with self.assertRaises(ValueError):
            self._order(comment='ааааааааааааааааа')

    def test_normal_comment_allowed(self):
        """Обычный комментарий принимается."""
        order = self._order(comment='Позвонить после 18:00, домофон 45')
        self.assertIn('домофон', order.comment)


class DuplicateItemsTest(TestCase):
    """Один товар несколькими строками не должен обходить проверку остатка.

    Ошибка была найдена при аудите: каждая строка заказа проверялась
    против полного остатка по отдельности, поэтому пять строк по три
    единицы проходили при остатке в три.
    """

    def setUp(self):
        self.wh = Warehouse.objects.create(name='Склад', type='finished')
        self.p = Product.objects.create(
            article_number='G-1', name='Мяч', category='equipment',
            size='5', color='белый', cost=300, selling_price=1800)
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=3)

    def test_duplicate_lines_are_summed(self):
        """Количество по одному товару складывается перед проверкой."""
        with self.assertRaises(InsufficientStockError):
            create_order('Иван Петров', '+79991234567',
                         [{'product': self.p.id, 'quantity': 3}] * 5)
        self.assertEqual(Order.objects.count(), 0)

    def test_duplicate_lines_within_stock_merge(self):
        """Повторы в пределах остатка объединяются в одну позицию."""
        order = create_order(
            'Иван Петров', '+79991234567',
            [{'product': self.p.id, 'quantity': 1},
             {'product': self.p.id, 'quantity': 2}])
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.first().quantity, Decimal('3'))
        self.assertEqual(get_available_quantity(self.p.id), Decimal('0'))

    def test_public_api_rejects_duplicate_overorder(self):
        """Через публичный API обойти проверку тоже нельзя."""
        from rest_framework.test import APIClient
        client = APIClient()
        r = client.post('/api/shop/orders/', {
            'customer_name': 'Иван Петров',
            'customer_phone': '+79991234567',
            'items': [{'product': self.p.id, 'quantity': 3}] * 5,
        }, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)
