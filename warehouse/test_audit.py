"""Регрессионные тесты по найденным при аудите дефектам.

Каждый тест здесь закрывает конкретную ошибку, которая была в системе.
Если правка когда-нибудь откатится, соответствующий тест это покажет.
"""
import decimal
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from inventory.models import Inventory, InventoryItem
from warehouse.models import (InboundDocument, Material, Order, OrderItem,
                              OutboundDocument, Product, Stock, Supplier,
                              Warehouse)

User = get_user_model()


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'auditor', password='x', role='admin', is_superuser=True)
        self.c = APIClient()
        self.c.force_authenticate(self.user)
        self.anon = APIClient()
        self.wh = Warehouse.objects.create(name='Основной', type='finished')
        self.wh2 = Warehouse.objects.create(name='Второй', type='finished')
        self.p = Product.objects.create(
            article_number='A-1', name='Ботинки', category='shoes',
            size='42', color='чёрный', cost=100, selling_price=200)


# --- Постраничная выдача ----------------------------------------------------
class PaginationLimitTest(Base):
    def test_limit_is_capped(self):
        """?limit= ограничен сверху: ключ MAX_LIMIT в settings DRF не читал."""
        from rest_framework.settings import api_settings
        self.assertEqual(api_settings.DEFAULT_PAGINATION_CLASS.max_limit, 500)
        r = self.c.get('/api/products/?limit=999999')
        self.assertEqual(r.status_code, 200)

    def test_normal_limit_still_works(self):
        for i in range(5):
            Product.objects.create(
                article_number=f'B-{i}', name=f'Т{i}', category='shoes',
                size='40', color='синий', cost=1, selling_price=2)
        r = self.c.get('/api/products/?limit=3')
        self.assertEqual(len(r.data['results']), 3)


# --- Целостность остатков ---------------------------------------------------
class StockConstraintTest(Base):
    def test_duplicate_stock_rows_rejected(self):
        """Дубль позиции склада запрещён: NULL ломал прежнее ограничение."""
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=10)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Stock.objects.create(
                    warehouse=self.wh, product=self.p, quantity=5)

    def test_duplicate_material_rows_rejected(self):
        m = Material.objects.create(name='Ткань', unit='m', category='textile',
                                    reorder_point=0)
        Stock.objects.create(warehouse=self.wh, material=m, quantity=10)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Stock.objects.create(
                    warehouse=self.wh, material=m, quantity=5)

    def test_same_product_on_other_warehouse_allowed(self):
        """Ограничение не мешает держать товар на разных складах."""
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=10)
        Stock.objects.create(warehouse=self.wh2, product=self.p, quantity=5)
        self.assertEqual(Stock.objects.filter(product=self.p).count(), 2)

    def test_stock_without_item_rejected(self):
        """Остаток обязан относиться к материалу или продукции."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Stock.objects.create(warehouse=self.wh, quantity=7)

    def test_stock_with_both_rejected(self):
        m = Material.objects.create(name='Кожа', unit='m2',
                                    category='leather', reorder_point=0)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Stock.objects.create(warehouse=self.wh, material=m,
                                     product=self.p, quantity=7)


# --- Публичное оформление заказа --------------------------------------------
class ShopInputTest(Base):
    def _order(self, payload):
        return self.anon.post('/api/shop/orders/', payload, format='json')

    def setUp(self):
        super().setUp()
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=10)
        self.ok = {'customer_name': 'Иван Петров',
                   'customer_phone': '+79991234567'}

    def test_missing_product_key(self):
        r = self._order({**self.ok, 'items': [{'quantity': 1}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn('товар', r.data['error'].lower())

    def test_non_numeric_quantity(self):
        r = self._order({
            **self.ok, 'items': [{'product': self.p.id, 'quantity': 'abc'}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn('количество', r.data['error'].lower())

    def test_items_as_string(self):
        r = self._order({**self.ok, 'items': 'abc'})
        self.assertEqual(r.status_code, 400)

    def test_items_empty(self):
        r = self._order({**self.ok, 'items': []})
        self.assertEqual(r.status_code, 400)

    def test_item_not_a_dict(self):
        r = self._order({**self.ok, 'items': [1, 2, 3]})
        self.assertEqual(r.status_code, 400)

    def test_non_numeric_product_id(self):
        r = self._order({
            **self.ok, 'items': [{'product': 'abc', 'quantity': 1}]})
        self.assertEqual(r.status_code, 400)

    def test_negative_quantity(self):
        r = self._order({
            **self.ok, 'items': [{'product': self.p.id, 'quantity': -5}]})
        self.assertEqual(r.status_code, 400)

    def test_infinite_quantity(self):
        r = self._order({
            **self.ok, 'items': [{'product': self.p.id, 'quantity': 'NaN'}]})
        self.assertEqual(r.status_code, 400)

    def test_valid_order_still_works(self):
        r = self._order({
            **self.ok, 'items': [{'product': self.p.id, 'quantity': 2}]})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(float(r.data['total']), 400.0)

    def test_duplicate_lines_are_summed(self):
        """Один товар двумя строками не должен обходить проверку остатка."""
        r = self._order({**self.ok, 'items': [
            {'product': self.p.id, 'quantity': 6},
            {'product': self.p.id, 'quantity': 6}]})
        self.assertEqual(r.status_code, 400)


# --- Номер заказа -----------------------------------------------------------
class OrderNumberTest(Base):
    def test_number_not_reused_after_delete(self):
        """Номер берётся от наибольшего выданного, а не от последнего id."""
        from warehouse.order_services import _generate_order_number
        o = Order.objects.create(number='ЗАК-00007', customer_name='А',
                                 customer_phone='+79990000001')
        self.assertEqual(_generate_order_number(), 'ЗАК-00008')
        o.delete()
        self.assertEqual(_generate_order_number(), 'ЗАК-00001')

    def test_collision_is_retried(self):
        """Занятый номер не роняет оформление — берётся следующий."""
        from warehouse.order_services import create_order
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=100)
        Order.objects.create(number='ЗАК-00001', customer_name='Занято',
                             customer_phone='+79990000009')
        order = create_order('Иван Петров', '+79991234567',
                             [{'product': self.p.id, 'quantity': 1}])
        self.assertEqual(order.number, 'ЗАК-00002')


# --- Статусы заказа ---------------------------------------------------------
class OrderStatusTest(Base):
    def setUp(self):
        super().setUp()
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=10)
        self.order = Order.objects.create(
            number='ЗАК-00001', customer_name='А',
            customer_phone='+79990000001')
        OrderItem.objects.create(order=self.order, product=self.p,
                                 quantity=3, price=200)

    def test_patch_status_forbidden(self):
        """Статус нельзя выставить в обход отгрузки."""
        r = self.c.patch(f'/api/orders/{self.order.id}/',
                         {'status': 'shipped'}, format='json')
        self.assertEqual(r.status_code, 405)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'new')

    def test_delete_forbidden(self):
        r = self.c.delete(f'/api/orders/{self.order.id}/')
        self.assertEqual(r.status_code, 405)
        self.assertTrue(Order.objects.filter(id=self.order.id).exists())

    def test_ship_writes_off_and_marks(self):
        """Отгрузка через действие по-прежнему работает и списывает товар."""
        r = self.c.post(f'/api/orders/{self.order.id}/ship/',
                        {'warehouse': self.wh.id}, format='json')
        self.assertEqual(r.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'shipped')
        self.assertIsNotNone(self.order.outbound_document)
        self.assertEqual(
            Stock.objects.get(warehouse=self.wh, product=self.p).quantity,
            Decimal('7'))

    def test_ship_bad_warehouse(self):
        r = self.c.post(f'/api/orders/{self.order.id}/ship/',
                        {'warehouse': 99999}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('склад', r.data['error'].lower())


# --- Наличие по складам -----------------------------------------------------
class WarehouseCoverageTest(Base):
    def test_split_stock_gives_actionable_error(self):
        """Товар на двух складах: отгрузка объясняет, где лежит остальное."""
        from warehouse.order_services import create_order
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=3)
        Stock.objects.create(warehouse=self.wh2, product=self.p, quantity=3)
        order = create_order('Иван Петров', '+79991234567',
                             [{'product': self.p.id, 'quantity': 5}])
        r = self.c.post(f'/api/orders/{order.id}/ship/',
                        {'warehouse': self.wh.id}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Второй', r.data['error'])
        # Документ отгрузки не должен остаться после неудачи
        self.assertFalse(OutboundDocument.objects.exists())
        order.refresh_from_db()
        self.assertEqual(order.status, 'new')


# --- Уведомление о низком остатке -------------------------------------------
class ReorderSignalTest(Base):
    """Письмо отправляется в transaction.on_commit, поэтому во всех тестах
    ниже сохранения обёрнуты в captureOnCommitCallbacks: без него коллбэки
    внутри TestCase не выполняются и проверять было бы нечего."""

    def test_counts_total_across_warehouses(self):
        """Дефицит считается по сумме складов — как и в отчётах."""
        m = Material.objects.create(name='Ткань', unit='m', category='textile',
                                    reorder_point=Decimal('100'))
        mail.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            Stock.objects.create(warehouse=self.wh, material=m, quantity=60)
            Stock.objects.create(warehouse=self.wh2, material=m, quantity=60)
        r = self.c.get('/api/materials/low_stock/')
        self.assertEqual(len(r.data), 0)
        self.assertEqual(len(mail.outbox), 0,
                         'Суммарно 120 при минимуме 100 — не дефицит')

    def test_notifies_on_crossing(self):
        """Письмо уходит при пересечении порога."""
        m = Material.objects.create(name='Кожа', unit='m2',
                                    category='leather',
                                    reorder_point=Decimal('100'))
        st = Stock.objects.create(warehouse=self.wh, material=m, quantity=200)
        mail.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            st.quantity = Decimal('50')
            st.save()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Кожа', mail.outbox[0].subject)

    def test_no_repeat_while_already_low(self):
        """Повторных писем об известном дефиците быть не должно."""
        m = Material.objects.create(name='Кожа', unit='m2',
                                    category='leather',
                                    reorder_point=Decimal('100'))
        st = Stock.objects.create(warehouse=self.wh, material=m, quantity=200)
        with self.captureOnCommitCallbacks(execute=True):
            st.quantity = Decimal('50')
            st.save()
        mail.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            for _ in range(5):
                st.quantity -= 1
                st.save()
        self.assertEqual(len(mail.outbox), 0)

    def test_no_mail_when_rolled_back(self):
        """Откат операции не должен оставлять письмо о несуществующей нехватке."""
        m = Material.objects.create(name='Замша', unit='m2',
                                    category='leather',
                                    reorder_point=Decimal('100'))
        st = Stock.objects.create(warehouse=self.wh, material=m, quantity=200)
        mail.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    st.quantity = Decimal('10')
                    st.save()
                    raise RuntimeError('операция сорвалась')
            except RuntimeError:
                pass
        self.assertEqual(len(mail.outbox), 0)


# --- Отчёты -----------------------------------------------------------------
class ReportTest(Base):
    def test_movement_report_bad_days(self):
        r = self.c.get('/api/reports/movement/?days=abc')
        self.assertEqual(r.status_code, 400)

    def test_movement_report_days_clamped(self):
        r = self.c.get('/api/reports/movement/?days=99999')
        self.assertEqual(r.status_code, 200)

    def test_inventory_report_bad_id(self):
        r = self.c.get('/api/reports/inventory/?inventory=abc')
        self.assertEqual(r.status_code, 400)

    def test_stock_report_bad_date(self):
        r = self.c.get('/api/reports/stock/?date=не-дата')
        self.assertEqual(r.status_code, 400)

    def test_stock_report_today(self):
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=10)
        r = self.c.get('/api/reports/stock/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['на_сегодня'])
        self.assertEqual(r.data['строки'][0]['количество'], 10.0)

    def test_stock_report_past_date_rolls_back_movements(self):
        """Остаток на прошедшую дату восстанавливается по журналу движения."""
        from warehouse.services import process_inbound_document
        Stock.objects.create(warehouse=self.wh, product=self.p, quantity=10)
        sup = Supplier.objects.create(name='Поставщик')
        doc = InboundDocument.objects.create(
            doc_number='П-1', doc_date='2026-01-01',
            supplier=sup, warehouse=self.wh)
        doc.items.create(product=self.p, quantity=90, unit_price=1)
        process_inbound_document(doc.id)   # сегодня стало 100

        self.assertEqual(
            Stock.objects.get(warehouse=self.wh, product=self.p).quantity,
            Decimal('100'))

        r = self.c.get('/api/reports/stock/?date=2020-01-01')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data['на_сегодня'])
        self.assertEqual(r.data['строки'][0]['количество'], 10.0,
                         'Приход, сделанный позже, должен быть отмотан назад')


# --- Чат --------------------------------------------------------------------
class ChatTest(Base):
    def test_bad_with_param(self):
        r = self.c.get('/api/chat/messages/?with=abc')
        self.assertEqual(r.status_code, 400)

    def test_bad_after_param(self):
        r = self.c.get('/api/chat/messages/?after=abc')
        self.assertEqual(r.status_code, 400)

    def test_message_to_missing_user_rejected(self):
        r = self.c.post('/api/chat/messages/',
                        {'text': 'привет', 'recipient': 999}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_mark_read_bad_peer(self):
        r = self.c.post('/api/chat/read/', {'with': 'abc'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_general_chat_still_works(self):
        r = self.c.post('/api/chat/messages/', {'text': 'всем привет'},
                        format='json')
        self.assertEqual(r.status_code, 201)
        r = self.c.get('/api/chat/messages/')
        self.assertEqual(len(r.data), 1)

    def test_private_chat_still_works(self):
        peer = User.objects.create_user('peer', password='x')
        r = self.c.post('/api/chat/messages/',
                        {'text': 'лично', 'recipient': peer.id},
                        format='json')
        self.assertEqual(r.status_code, 201)
        r = self.c.get(f'/api/chat/messages/?with={peer.id}')
        self.assertEqual(len(r.data), 1)


# --- Инвентаризация ---------------------------------------------------------
class InventoryTest(Base):
    def _sheet(self, number='И-1', quantity=10):
        inv = Inventory.objects.create(number=number, warehouse=self.wh)
        Stock.objects.create(warehouse=self.wh, product=self.p,
                             quantity=quantity)
        self.c.post(f'/api/inventories/{inv.id}/build_sheet/')
        return inv, InventoryItem.objects.get(inventory=inv)

    def test_save_counts_rejects_negative(self):
        inv, item = self._sheet()
        r = self.c.post(f'/api/inventories/{inv.id}/save_counts/',
                        {'counts': [{'item_id': item.id,
                                     'actual_quantity': -50}]}, format='json')
        self.assertEqual(r.status_code, 400)
        item.refresh_from_db()
        self.assertIsNone(item.actual_quantity)

    def test_save_counts_rejects_garbage(self):
        inv, item = self._sheet()
        r = self.c.post(f'/api/inventories/{inv.id}/save_counts/',
                        {'counts': [{'item_id': item.id,
                                     'actual_quantity': 'abc'}]},
                        format='json')
        self.assertEqual(r.status_code, 400)

    def test_save_counts_accepts_valid(self):
        inv, item = self._sheet()
        r = self.c.post(f'/api/inventories/{inv.id}/save_counts/',
                        {'counts': [{'item_id': item.id,
                                     'actual_quantity': 8}]}, format='json')
        self.assertEqual(r.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.actual_quantity, Decimal('8'))

    def test_completed_sheet_not_rebuilt(self):
        inv, item = self._sheet()
        self.c.post(f'/api/inventories/{inv.id}/finalize/')
        r = self.c.post(f'/api/inventories/{inv.id}/build_sheet/')
        self.assertEqual(r.status_code, 400)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'completed')

    def test_finalize_preserves_concurrent_movements(self):
        """Приход между описью и завершением не должен теряться."""
        from warehouse.services import process_inbound_document
        inv, item = self._sheet(quantity=10)
        self.c.post(f'/api/inventories/{inv.id}/save_counts/',
                    {'counts': [{'item_id': item.id,
                                 'actual_quantity': 12}]}, format='json')

        sup = Supplier.objects.create(name='Поставщик')
        d = InboundDocument.objects.create(
            doc_number='П-1', doc_date='2026-01-01',
            supplier=sup, warehouse=self.wh)
        d.items.create(product=self.p, quantity=100, unit_price=1)
        process_inbound_document(d.id)     # склад стал 110

        r = self.c.post(f'/api/inventories/{inv.id}/finalize/')
        self.assertEqual(r.status_code, 200)
        st = Stock.objects.get(warehouse=self.wh, product=self.p)
        self.assertEqual(st.quantity, Decimal('112'),
                         'К остатку применяется расхождение (+2), '
                         'а не результат пересчёта поверх')

    def test_adjust_movement_is_signed(self):
        """Корректировка пишется со знаком — иначе не отмотать остаток."""
        from warehouse.models import StockMovement
        inv, item = self._sheet(quantity=10)
        self.c.post(f'/api/inventories/{inv.id}/save_counts/',
                    {'counts': [{'item_id': item.id,
                                 'actual_quantity': 7}]}, format='json')
        self.c.post(f'/api/inventories/{inv.id}/finalize/')
        move = StockMovement.objects.get(movement_type='adjust')
        self.assertEqual(move.quantity, Decimal('-3'))


# --- Производство -----------------------------------------------------------
class ProduceTest(Base):
    def test_missing_product(self):
        r = self.c.post('/api/produce/', {
            'product': 99999, 'quantity': 1,
            'product_warehouse': self.wh.id,
            'material_warehouse': self.wh.id, 'materials': []}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_bad_quantity(self):
        r = self.c.post('/api/produce/', {
            'product': self.p.id, 'quantity': 'abc',
            'product_warehouse': self.wh.id,
            'material_warehouse': self.wh.id, 'materials': []}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_bad_warehouse(self):
        r = self.c.post('/api/produce/', {
            'product': self.p.id, 'quantity': 1,
            'product_warehouse': 99999,
            'material_warehouse': self.wh.id, 'materials': []}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_valid_production_works(self):
        m = Material.objects.create(name='Кожа', unit='m2',
                                    category='leather', reorder_point=0)
        Stock.objects.create(warehouse=self.wh2, material=m, quantity=50)
        r = self.c.post('/api/produce/', {
            'product': self.p.id, 'quantity': 3,
            'product_warehouse': self.wh.id,
            'material_warehouse': self.wh2.id,
            'materials': [{'material': m.id, 'quantity': 6}]}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            Stock.objects.get(warehouse=self.wh, product=self.p).quantity,
            Decimal('3'))
        self.assertEqual(
            Stock.objects.get(warehouse=self.wh2, material=m).quantity,
            Decimal('44'))


# --- Детектор имён ----------------------------------------------------------
class NameValidationTest(TestCase):
    def test_real_names_accepted(self):
        """Реальные, но нечастые имена не должны отклоняться."""
        from warehouse.order_services import validate_customer_name
        for name in ['Аркадий Швыдкой', 'Пров Тряпицын', 'Феликс Эдмундович',
                     'Эжен Шмидт', 'Лев Гмыря', 'Ольга Исаева',
                     'Юхан Тыниссон', 'Гурген Мкртчян', 'Иван Иванов']:
            with self.subTest(name=name):
                self.assertEqual(validate_customer_name(name), name)

    def test_real_addresses_accepted(self):
        from warehouse.order_services import validate_russian_address
        for addr in ['г. Йошкар-Ола, ул. Кырля, д. 3',
                     'г. Грозный, пр. Хусейна Исаева, д. 5',
                     'г. Улан-Удэ, ул. Жердева, д. 15',
                     'г. Дмитров, ул. Советская, д. 10, кв. 5']:
            with self.subTest(addr=addr):
                validate_russian_address(addr)   # не должно возбуждать

    def test_gibberish_still_rejected(self):
        from warehouse.order_services import validate_customer_name
        for junk in ['фывфыв ячсмить', 'йцукенгш ждлоаы', 'ааааа бббббб']:
            with self.subTest(junk=junk):
                with self.assertRaises(ValueError):
                    validate_customer_name(junk)
