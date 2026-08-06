"""Проверка расчёта фактической себестоимости производства.

Раньше производство списывало материалы, приходовало продукцию и не
оставляло следа: восстановить, из чего сделана партия и во что она
обошлась, было нельзя. Эти тесты закрепляют, что теперь себестоимость
считается — и что она честно сообщает о своей неполноте, когда цен не
хватает.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from warehouse.models import (InboundDocument, Material,
                              ProductionRun, Product, Stock, Supplier,
                              Warehouse)
from warehouse.services import process_inbound_document, produce_product

User = get_user_model()


class ProductionCostBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'master', password='x', role='storekeeper')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.raw = Warehouse.objects.create(name='Сырьё', type='raw')
        self.finished = Warehouse.objects.create(name='Продукция',
                                                 type='finished')
        self.supplier = Supplier.objects.create(name='Поставщик')

        self.leather = Material.objects.create(
            name='Кожа', unit='m2', category='leather',
            reorder_point=Decimal('1'))
        self.sole = Material.objects.create(
            name='Подошва', unit='pc', category='polymer',
            reorder_point=Decimal('1'))

        # Плановая себестоимость в карточке — 1000
        self.product = Product.objects.create(
            article_number='B-1', name='Ботинки', category='shoes',
            size='42', color='чёрный', cost=Decimal('1000'),
            selling_price=Decimal('2500'))

    def buy(self, material, quantity, price, number):
        """Купить материал: остаток и запись в истории цен."""
        document = InboundDocument.objects.create(
            doc_number=number, doc_date='2026-01-01',
            supplier=self.supplier, warehouse=self.raw)
        document.items.create(material=material, quantity=Decimal(quantity),
                              unit_price=Decimal(price))
        process_inbound_document(document.id)


class CostCalculationTest(ProductionCostBase):
    def test_cost_from_last_purchase_prices(self):
        """Себестоимость = сумма материалов по последним ценам / выпуск."""
        self.buy(self.leather, '100', '500', 'П-1')   # 500 за м²
        self.buy(self.sole, '100', '200', 'П-2')      # 200 за штуку

        run = produce_product(
            product_id=self.product.id, quantity=10,
            product_warehouse_id=self.finished.id,
            material_warehouse_id=self.raw.id,
            materials=[{'material': self.leather.id, 'quantity': 20},
                       {'material': self.sole.id, 'quantity': 10}],
            user=self.user)

        # 20 × 500 + 10 × 200 = 12 000 на 10 пар → 1200 за пару
        self.assertEqual(run.material_cost, Decimal('12000.00'))
        self.assertEqual(run.unit_cost, Decimal('1200.00'))
        self.assertTrue(run.pricing_complete)

    def test_uses_latest_price_not_first(self):
        """Материал дорожал — берётся последняя цена, а не первая."""
        self.buy(self.leather, '50', '400', 'П-1')
        self.buy(self.leather, '50', '600', 'П-2')

        run = produce_product(
            product_id=self.product.id, quantity=1,
            product_warehouse_id=self.finished.id,
            material_warehouse_id=self.raw.id,
            materials=[{'material': self.leather.id, 'quantity': 2}],
            user=self.user)
        self.assertEqual(run.unit_cost, Decimal('1200.00'))

    def test_deviation_from_plan(self):
        """Отклонение от плановой себестоимости считается и в рублях, и в %."""
        self.buy(self.leather, '100', '600', 'П-1')
        run = produce_product(
            product_id=self.product.id, quantity=1,
            product_warehouse_id=self.finished.id,
            material_warehouse_id=self.raw.id,
            materials=[{'material': self.leather.id, 'quantity': 2}],
            user=self.user)

        # факт 1200, план 1000 → +200, то есть +20%
        self.assertEqual(run.planned_unit_cost, Decimal('1000.00'))
        self.assertEqual(run.cost_deviation, Decimal('200.00'))
        self.assertEqual(run.cost_deviation_percent, Decimal('20.00'))

    def test_unknown_price_flagged(self):
        """Материал без закупок — себестоимость занижена, и это видно."""
        Stock.objects.create(warehouse=self.raw, material=self.leather,
                             quantity=Decimal('100'))
        run = produce_product(
            product_id=self.product.id, quantity=1,
            product_warehouse_id=self.finished.id,
            material_warehouse_id=self.raw.id,
            materials=[{'material': self.leather.id, 'quantity': 2}],
            user=self.user)

        self.assertFalse(run.pricing_complete)
        self.assertEqual(run.material_cost, Decimal('0.00'))
        line = run.materials.first()
        self.assertFalse(line.price_known)

    def test_partial_pricing_flagged(self):
        """Хотя бы один материал без цены — признак снимается."""
        self.buy(self.leather, '100', '500', 'П-1')
        Stock.objects.create(warehouse=self.raw, material=self.sole,
                             quantity=Decimal('100'))
        run = produce_product(
            product_id=self.product.id, quantity=1,
            product_warehouse_id=self.finished.id,
            material_warehouse_id=self.raw.id,
            materials=[{'material': self.leather.id, 'quantity': 1},
                       {'material': self.sole.id, 'quantity': 1}],
            user=self.user)
        self.assertFalse(run.pricing_complete)
        self.assertEqual(run.material_cost, Decimal('500.00'))

    def test_price_frozen_in_line(self):
        """Цена сохраняется в строке и не меняется при новой закупке."""
        self.buy(self.leather, '100', '500', 'П-1')
        run = produce_product(
            product_id=self.product.id, quantity=1,
            product_warehouse_id=self.finished.id,
            material_warehouse_id=self.raw.id,
            materials=[{'material': self.leather.id, 'quantity': 1}],
            user=self.user)

        self.buy(self.leather, '100', '900', 'П-2')   # подорожало вдвое

        run.refresh_from_db()
        self.assertEqual(run.materials.first().unit_price, Decimal('500.00'))
        self.assertEqual(run.unit_cost, Decimal('500.00'),
                         'Себестоимость прошлой партии не должна '
                         'пересчитываться по новым ценам')


class ProductionRunRecordTest(ProductionCostBase):
    def test_run_records_composition(self):
        """Выпуск сохраняет состав — раньше это было невосстановимо."""
        self.buy(self.leather, '100', '500', 'П-1')
        self.buy(self.sole, '100', '200', 'П-2')
        run = produce_product(
            product_id=self.product.id, quantity=5,
            product_warehouse_id=self.finished.id,
            material_warehouse_id=self.raw.id,
            materials=[{'material': self.leather.id, 'quantity': 10},
                       {'material': self.sole.id, 'quantity': 5}],
            user=self.user)

        self.assertEqual(run.materials.count(), 2)
        self.assertEqual(run.product, self.product)
        self.assertEqual(run.quantity, Decimal('5.00'))
        self.assertEqual(run.created_by, self.user)

    def test_numbering(self):
        self.buy(self.leather, '100', '500', 'П-1')
        numbers = []
        for _ in range(3):
            run = produce_product(
                product_id=self.product.id, quantity=1,
                product_warehouse_id=self.finished.id,
                material_warehouse_id=self.raw.id,
                materials=[{'material': self.leather.id, 'quantity': 1}],
                user=self.user)
            numbers.append(run.number)
        self.assertEqual(numbers, ['ВЫП-00001', 'ВЫП-00002', 'ВЫП-00003'])

    def test_failed_production_leaves_no_run(self):
        """При нехватке материала откатывается и документ выпуска."""
        from warehouse.services import InsufficientStockError

        self.buy(self.leather, '1', '500', 'П-1')
        with self.assertRaises(InsufficientStockError):
            produce_product(
                product_id=self.product.id, quantity=1,
                product_warehouse_id=self.finished.id,
                material_warehouse_id=self.raw.id,
                materials=[{'material': self.leather.id, 'quantity': 999}],
                user=self.user)
        self.assertEqual(ProductionRun.objects.count(), 0)


class ProductionApiTest(ProductionCostBase):
    def test_produce_returns_cost(self):
        """Кладовщик видит себестоимость сразу, не заходя в отчёты."""
        self.buy(self.leather, '100', '500', 'П-1')
        response = self.client.post('/api/produce/', {
            'product': self.product.id, 'quantity': 2,
            'product_warehouse': self.finished.id,
            'material_warehouse': self.raw.id,
            'materials': [{'material': self.leather.id, 'quantity': 4}]},
            format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['unit_cost'], 1000.0)
        self.assertEqual(response.data['planned_unit_cost'], 1000.0)
        self.assertEqual(response.data['cost_deviation'], 0.0)
        self.assertTrue(response.data['pricing_complete'])
        self.assertEqual(response.data['run_number'], 'ВЫП-00001')

    def test_cost_report(self):
        self.buy(self.leather, '100', '600', 'П-1')
        self.client.post('/api/produce/', {
            'product': self.product.id, 'quantity': 1,
            'product_warehouse': self.finished.id,
            'material_warehouse': self.raw.id,
            'materials': [{'material': self.leather.id, 'quantity': 2}]},
            format='json')

        response = self.client.get('/api/reports/production-cost/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['выпусков'], 1)
        self.assertEqual(response.data['дороже_плана'], 1)

        row = response.data['строки'][0]
        self.assertEqual(row['себестоимость_факт'], 1200.0)
        self.assertEqual(row['себестоимость_план'], 1000.0)
        self.assertEqual(row['отклонение_процент'], 20.0)
        self.assertEqual(len(row['материалы']), 1)

    def test_report_bad_days(self):
        response = self.client.get('/api/reports/production-cost/?days=скоро')
        self.assertEqual(response.status_code, 400)

    def test_report_warns_about_scope(self):
        """Отчёт честно говорит, что в себестоимость входят только материалы."""
        response = self.client.get('/api/reports/production-cost/')
        self.assertIn('только материалы', response.data['примечание'])

    def test_requires_authentication(self):
        response = APIClient().get('/api/reports/production-cost/')
        self.assertIn(response.status_code, (401, 403))
