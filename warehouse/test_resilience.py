"""Проверки, закрывающие находки сплошной проверки на отказоустойчивость.

Три группы.

1. Раздвоение остатка. Запрет «одна строка на позицию склада» не работал:
   он был задан по тройке полей, а у строки остатка одно из двух полей
   всегда пустое, и в SQL пустое значение не равно другому пустому. База
   считала такие строки разными. Два одновременных прихода на позицию,
   которой ещё нет на складе, заводили две строки остатка — и позиция
   ломалась насовсем: списание ищет одну строку, находит две и падает.

2. Производство. В теле запроса могло прийти что угодно — количество
   «абв», список материалов строкой, номер удалённого товара. Ни одно из
   этого не проверялось, и кладовщик видел «ошибка сервера» вместо
   объяснения.

3. Инвентаризация и чат. Та же беда: присланное шло прямо в базу.
   Пересчёт «абв» ронял обработчик, пересчёт «−50» записывался молча и
   уводил остаток в минус при завершении описи.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from inventory.models import Inventory, InventoryItem
from .models import Material, Product, Stock, Warehouse

User = get_user_model()


def make_material(name='Кожа', **kwargs):
    values = dict(name=name, unit='pc', category='leather',
                  reorder_point=Decimal('1'))
    values.update(kwargs)
    return Material.objects.create(**values)


def make_product(**kwargs):
    values = dict(article_number='МЯ-1', name='Мяч футбольный',
                  category='equipment', size='5', color='белый',
                  cost=Decimal('300'), selling_price=Decimal('1500'))
    values.update(kwargs)
    return Product.objects.create(**values)


class StockPositionIsUniqueTest(TestCase):
    """Одна позиция склада — одна строка остатка, и это сторожит база.

    Проверять запрет надо именно на уровне базы: только он работает,
    когда двое проводят документы одновременно. Проверка в коде в такой
    момент бесполезна — оба видят «строки ещё нет» и заводят каждый свою.
    """

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='Сырьё', type='raw')
        self.material = make_material()
        self.product = make_product()

    def test_duplicate_material_position_is_refused(self):
        """Главная проверка: прежний запрет этого не ловил."""
        Stock.objects.create(warehouse=self.warehouse,
                             material=self.material, quantity=Decimal('5'))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Stock.objects.create(warehouse=self.warehouse,
                                     material=self.material,
                                     quantity=Decimal('7'))

    def test_duplicate_product_position_is_refused(self):
        Stock.objects.create(warehouse=self.warehouse,
                             product=self.product, quantity=Decimal('5'))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Stock.objects.create(warehouse=self.warehouse,
                                     product=self.product,
                                     quantity=Decimal('7'))

    def test_same_material_on_another_warehouse_is_fine(self):
        """Запрет не должен мешать обычному делу: один материал лежит
        на нескольких складах, и это разные строки."""
        other = Warehouse.objects.create(name='Запасной', type='raw')
        Stock.objects.create(warehouse=self.warehouse,
                             material=self.material, quantity=Decimal('5'))
        Stock.objects.create(warehouse=other, material=self.material,
                             quantity=Decimal('3'))
        self.assertEqual(Stock.objects.filter(material=self.material).count(),
                         2)

    def test_material_and_product_do_not_collide(self):
        """Материал и продукция на одном складе — тоже разные строки."""
        Stock.objects.create(warehouse=self.warehouse,
                             material=self.material, quantity=Decimal('5'))
        Stock.objects.create(warehouse=self.warehouse,
                             product=self.product, quantity=Decimal('5'))
        self.assertEqual(Stock.objects.filter(
            warehouse=self.warehouse).count(), 2)

    def test_second_receipt_lands_on_the_same_row(self):
        """Проведение второго прихода складывается с первым, а не
        заводит вторую строку — ради этого запрет и нужен."""
        from .services import process_inbound_document
        from .models import InboundDocument, InboundItem, Supplier

        supplier = Supplier.objects.create(name='Поставщик')
        for number in ('П-1', 'П-2'):
            doc = InboundDocument.objects.create(
                doc_number=number, doc_date='2026-01-01',
                warehouse=self.warehouse, supplier=supplier)
            InboundItem.objects.create(inbound_doc=doc,
                                       material=self.material,
                                       quantity=Decimal('5'),
                                       unit_price=Decimal('10'))
            process_inbound_document(doc.pk)

        rows = Stock.objects.filter(warehouse=self.warehouse,
                                    material=self.material)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().quantity, Decimal('10'))


class ProduceInputTest(TestCase):
    """Выпуск продукции: в запросе может прийти что угодно.

    Ожидание везде одно — отказ 400 с объяснением, а не 500.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='kladovshik', password='x', role='storekeeper')
        self.client.force_login(self.user)
        self.raw = Warehouse.objects.create(name='Сырьё', type='raw')
        self.finished = Warehouse.objects.create(name='Готовая',
                                                 type='finished')
        self.material = make_material()
        self.product = make_product()
        Stock.objects.create(warehouse=self.raw, material=self.material,
                             quantity=Decimal('100'))

    def produce(self, **overrides):
        payload = {
            'product': self.product.id,
            'quantity': 1,
            'product_warehouse': self.finished.id,
            'material_warehouse': self.raw.id,
            'materials': [{'material': self.material.id, 'quantity': 2}],
        }
        payload.update(overrides)
        return self.client.post('/api/produce/', payload,
                                content_type='application/json')

    def test_normal_production_still_works(self):
        response = self.produce()
        self.assertEqual(response.status_code, 200)
        stock = Stock.objects.get(warehouse=self.finished,
                                  product=self.product)
        self.assertEqual(stock.quantity, Decimal('1'))

    def test_text_quantity_is_refused_politely(self):
        self.assertEqual(self.produce(quantity='абв').status_code, 400)

    def test_nan_quantity_is_refused(self):
        """NaN переводится в число успешно, но сравнение с нулём для него
        всегда ложно — проверку «больше нуля» он проходил насквозь."""
        self.assertEqual(self.produce(quantity='NaN').status_code, 400)

    def test_infinite_quantity_is_refused(self):
        self.assertEqual(self.produce(quantity='Infinity').status_code, 400)

    def test_materials_as_text_is_refused(self):
        self.assertEqual(self.produce(materials='ой').status_code, 400)

    def test_materials_as_dict_is_refused(self):
        self.assertEqual(
            self.produce(materials={'material': 1}).status_code, 400)

    def test_material_row_without_quantity_is_refused(self):
        self.assertEqual(
            self.produce(materials=[{'material': self.material.id}])
            .status_code, 400)

    def test_text_material_quantity_is_refused(self):
        self.assertEqual(
            self.produce(materials=[{'material': self.material.id,
                                     'quantity': 'абв'}]).status_code, 400)

    def test_deleted_product_is_refused(self):
        """Страница могла быть открыта до того, как товар убрали."""
        self.assertEqual(self.produce(product=999999).status_code, 400)

    def test_deleted_material_is_refused(self):
        self.assertEqual(
            self.produce(materials=[{'material': 999999, 'quantity': 1}])
            .status_code, 400)

    def test_deleted_warehouse_is_refused(self):
        self.assertEqual(
            self.produce(product_warehouse=999999).status_code, 400)

    def test_nothing_is_written_when_input_is_bad(self):
        """Отказ не должен оставлять следов: ни выпуска, ни списания."""
        from .models import ProductionRun

        self.produce(materials=[{'material': self.material.id,
                                 'quantity': 'абв'}])
        self.assertEqual(ProductionRun.objects.count(), 0)
        stock = Stock.objects.get(warehouse=self.raw, material=self.material)
        self.assertEqual(stock.quantity, Decimal('100'))


class SaveCountsInputTest(TestCase):
    """Сохранение пересчёта при инвентаризации."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='kladovshik2', password='x', role='storekeeper')
        self.client.force_login(self.user)
        self.warehouse = Warehouse.objects.create(name='Сырьё', type='raw')
        self.material = make_material()
        Stock.objects.create(warehouse=self.warehouse,
                             material=self.material, quantity=Decimal('10'))
        self.inventory = Inventory.objects.create(
            number='ИНВ-1', warehouse=self.warehouse)
        self.item = InventoryItem.objects.create(
            inventory=self.inventory, material=self.material,
            system_quantity=Decimal('10'))

    def save(self, counts):
        return self.client.post(
            f'/api/inventories/{self.inventory.pk}/save_counts/',
            {'counts': counts}, content_type='application/json')

    def test_normal_count_is_saved(self):
        response = self.save([{'item_id': self.item.pk,
                               'actual_quantity': 8}])
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.actual_quantity, Decimal('8'))

    def test_empty_count_means_not_counted(self):
        """Пустое поле — позицию просто не считали, это не ошибка."""
        response = self.save([{'item_id': self.item.pk,
                               'actual_quantity': None}])
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.actual_quantity)

    def test_counts_as_text_is_refused(self):
        self.assertEqual(self.save('ой').status_code, 400)

    def test_counts_as_number_is_refused(self):
        self.assertEqual(self.save(5).status_code, 400)

    def test_rows_that_are_not_rows_are_refused(self):
        self.assertEqual(self.save([1, 2, 3]).status_code, 400)

    def test_text_count_is_refused(self):
        self.assertEqual(
            self.save([{'item_id': self.item.pk,
                        'actual_quantity': 'абв'}]).status_code, 400)

    def test_text_item_id_is_refused(self):
        self.assertEqual(
            self.save([{'item_id': 'абв',
                        'actual_quantity': 1}]).status_code, 400)

    def test_negative_count_is_refused(self):
        """На полке лежит либо что-то, либо ничего. Отрицательный
        пересчёт раньше записывался молча и уводил остаток в минус."""
        response = self.save([{'item_id': self.item.pk,
                               'actual_quantity': -50}])
        self.assertEqual(response.status_code, 400)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.actual_quantity)

    def test_bad_row_saves_nothing_at_all(self):
        """Проверка идёт до первой записи: одна плохая строка не должна
        оставить половину пересчёта сохранённой."""
        second = InventoryItem.objects.create(
            inventory=self.inventory, material=make_material('Замша'),
            system_quantity=Decimal('4'))
        self.save([{'item_id': self.item.pk, 'actual_quantity': 8},
                   {'item_id': second.pk, 'actual_quantity': 'абв'}])
        self.item.refresh_from_db()
        self.assertIsNone(self.item.actual_quantity)


class ChatInputTest(TestCase):
    """Чат сотрудников: номера собеседников приходят из адресной строки."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='kladovshik3', password='x', role='storekeeper')
        self.colleague = User.objects.create_user(
            username='ekonomist', password='x', role='economist')
        self.client.force_login(self.user)

    def test_message_to_colleague_still_works(self):
        response = self.client.post(
            '/api/chat/messages/',
            {'text': 'привет', 'recipient': self.colleague.pk},
            content_type='application/json')
        self.assertEqual(response.status_code, 201)

    def test_text_recipient_is_refused(self):
        response = self.client.post(
            '/api/chat/messages/', {'text': 'привет', 'recipient': 'абв'},
            content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_unknown_recipient_is_refused(self):
        response = self.client.post(
            '/api/chat/messages/', {'text': 'привет', 'recipient': 999999},
            content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_text_peer_in_address_is_refused(self):
        self.assertEqual(
            self.client.get('/api/chat/messages/?with=абв').status_code, 400)

    def test_text_marker_in_address_is_refused(self):
        self.assertEqual(
            self.client.get('/api/chat/messages/?after=абв').status_code, 400)

    def test_text_peer_in_mark_read_is_refused(self):
        response = self.client.post('/api/chat/read/', {'with': 'абв'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_reading_general_chat_still_works(self):
        self.assertEqual(
            self.client.get('/api/chat/messages/').status_code, 200)
