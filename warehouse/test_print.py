"""Проверка печатных форм.

Печатная форма — единственное, что уходит из системы на бумагу и живёт
дальше своей жизнью. Ошибка в ней обнаруживается на складе, когда
сверять уже нечего, поэтому здесь проверяются числа: количество, цена,
сумма строки и итог.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (InboundDocument, InboundItem, Material, Order,
                     OrderItem, OutboundDocument, OutboundItem, Product,
                     Supplier, Warehouse)

User = get_user_model()


class PrintTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='kladovshchik', password='x', role='storekeeper',
            first_name='Пётр', last_name='Петров')
        self.client.force_login(self.user)
        self.warehouse = Warehouse.objects.create(
            name='Основной', type='raw')
        self.material = Material.objects.create(
            name='Кожа хромовая', unit='m', category='leather',
            reorder_point=Decimal('5'))
        self.product = Product.objects.create(
            article_number='МЯ-1', name='Мяч футбольный', category='equipment',
            size='5', color='белый', cost=Decimal('300'),
            selling_price=Decimal('1500'))


class InboundPrintTest(PrintTestBase):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(name='ООО Поставка')
        self.document = InboundDocument.objects.create(
            doc_number='ПР-7', doc_date=date(2026, 3, 14),
            supplier=self.supplier, warehouse=self.warehouse,
            created_by=self.user)
        InboundItem.objects.create(
            inbound_doc=self.document, material=self.material,
            quantity=Decimal('12.50'), unit_price=Decimal('240.00'))

    def test_form_opens(self):
        response = self.client.get(f'/print/inbound/{self.document.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Приходная накладная')
        self.assertContains(response, 'ПР-7')

    def test_head_shows_where_it_came_from(self):
        response = self.client.get(f'/print/inbound/{self.document.pk}/')
        self.assertContains(response, 'ООО Поставка')
        self.assertContains(response, 'Основной')
        self.assertContains(response, '14.03.2026')

    def test_numbers_are_right(self):
        """12,5 × 240 = 3000. Ошибка здесь обнаружится на складе.

        Разделитель — запятая: документ русский, и «3000.00» в нём
        читалось бы как чужое.
        """
        response = self.client.get(f'/print/inbound/{self.document.pk}/')
        text = response.content.decode()
        self.assertIn('12,50', text)
        self.assertIn('240,00', text)
        self.assertIn('3000,00', text)

    def test_unit_comes_from_the_material(self):
        """У кожи метры, а не штуки: иначе на складе отмерят не то."""
        response = self.client.get(f'/print/inbound/{self.document.pk}/')
        self.assertContains(response, 'м')

    def test_unprocessed_document_is_marked(self):
        """Непроведённый документ на бумаге неотличим от проведённого.

        А это разные вещи: по одному остатки уже изменились, по другому
        нет. Отметка снимает вопрос.
        """
        response = self.client.get(f'/print/inbound/{self.document.pk}/')
        self.assertContains(response, 'Не проведён')

        self.document.processed = True
        self.document.save()
        response = self.client.get(f'/print/inbound/{self.document.pk}/')
        self.assertNotContains(response, 'Не проведён')

    def test_signature_lines_are_there(self):
        response = self.client.get(f'/print/inbound/{self.document.pk}/')
        self.assertContains(response, 'Сдал')
        self.assertContains(response, 'Принял')

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(f'/print/inbound/{self.document.pk}/')
        self.assertEqual(response.status_code, 302)

    def test_missing_document(self):
        self.assertEqual(
            self.client.get('/print/inbound/999999/').status_code, 404)

    def test_oversized_number_is_not_a_failure(self):
        """Номер шире разрядности базы уже ронял отчёты — здесь не должен."""
        response = self.client.get(f'/print/inbound/{2 ** 63}/')
        self.assertIn(response.status_code, (400, 404))


class OutboundPrintTest(PrintTestBase):
    def setUp(self):
        super().setUp()
        self.document = OutboundDocument.objects.create(
            doc_number='РС-3', doc_date=date(2026, 3, 15),
            warehouse=self.warehouse, purpose='production',
            production_order='ЗП-88', created_by=self.user)
        OutboundItem.objects.create(
            outbound_doc=self.document, product=self.product,
            quantity=Decimal('3'), unit_price=Decimal('1500.00'))

    def test_form_opens_with_purpose(self):
        response = self.client.get(f'/print/outbound/{self.document.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Расходная накладная')
        self.assertContains(response, 'В производство')
        self.assertContains(response, 'ЗП-88')

    def test_total_is_right(self):
        response = self.client.get(f'/print/outbound/{self.document.pk}/')
        self.assertContains(response, '4500,00')

    def test_product_is_counted_in_pieces(self):
        response = self.client.get(f'/print/outbound/{self.document.pk}/')
        self.assertContains(response, 'шт.')

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(f'/print/outbound/{self.document.pk}/')
            .status_code, 302)


class OrderPrintTest(PrintTestBase):
    def setUp(self):
        super().setUp()
        self.order = Order.objects.create(
            number='З-1001', customer_name='Иванов Иван',
            customer_phone='+7 900 000-00-00', address='Москва, ул. Мира, 1',
            comment='позвонить за час')
        OrderItem.objects.create(
            order=self.order, product=self.product,
            quantity=Decimal('2'), price=Decimal('1500.00'))

    def test_picking_list_has_what_to_collect(self):
        response = self.client.get(f'/print/order/{self.order.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Заказ покупателя')
        self.assertContains(response, 'Мяч футбольный')
        self.assertContains(response, '3000,00')

    def test_where_to_send_it(self):
        response = self.client.get(f'/print/order/{self.order.pk}/')
        self.assertContains(response, 'Иванов Иван')
        self.assertContains(response, 'Москва, ул. Мира, 1')
        self.assertContains(response, 'позвонить за час')

    def test_no_processing_mark_for_orders(self):
        """У заказа нет проведения — отметке о нём взяться неоткуда."""
        response = self.client.get(f'/print/order/{self.order.pk}/')
        self.assertNotContains(response, 'Не проведён')

    def test_empty_order_does_not_break_the_form(self):
        empty = Order.objects.create(
            number='З-1002', customer_name='Пустой',
            customer_phone='+7 900 111-11-11')
        response = self.client.get(f'/print/order/{empty.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'нет позиций')

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(f'/print/order/{self.order.pk}/').status_code, 302)
