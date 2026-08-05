"""
Тесты системы складского учёта.

Проверяют:
- обработку приходных и расходных документов (фрагменты 14-15);
- запрет отрицательных остатков;
- права доступа к API;
- корректность сериализации данных.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from warehouse.models import (InboundDocument, InboundItem, Material,
                              OutboundDocument, OutboundItem, Stock,
                              Supplier, Warehouse)
from warehouse.services import (InsufficientStockError,
                                process_inbound_document,
                                process_outbound_document)

User = get_user_model()


class InboundProcessingTest(TestCase):
    """Тест обработки приходного документа (фрагмент 14)."""

    def setUp(self):
        """Подготовка: создание тестовых объектов."""
        self.warehouse = Warehouse.objects.create(
            name='Склад сырья', type='raw')
        self.material = Material.objects.create(
            name='Ткань', unit='m', category='textile', reorder_point=10)
        self.supplier = Supplier.objects.create(name='Поставщик')

    def test_stock_increases(self):
        """При проведении прихода остаток должен увеличиться."""
        doc = InboundDocument.objects.create(
            doc_number='П-001', doc_date='2026-07-01',
            warehouse=self.warehouse, supplier=self.supplier)
        InboundItem.objects.create(
            inbound_doc=doc, material=self.material,
            quantity=100, unit_price=250)

        process_inbound_document(doc.pk)

        stock = Stock.objects.get(
            warehouse=self.warehouse, material=self.material)
        self.assertEqual(stock.quantity, 100)

    def test_document_marked_processed(self):
        """После проведения документ помечается как проведённый."""
        doc = InboundDocument.objects.create(
            doc_number='П-002', doc_date='2026-07-02',
            warehouse=self.warehouse, supplier=self.supplier)
        InboundItem.objects.create(
            inbound_doc=doc, material=self.material,
            quantity=50, unit_price=100)

        process_inbound_document(doc.pk)

        doc.refresh_from_db()
        self.assertTrue(doc.processed)

    def test_cannot_process_twice(self):
        """Проведённый документ нельзя обработать дважды."""
        doc = InboundDocument.objects.create(
            doc_number='П-003', doc_date='2026-07-03',
            warehouse=self.warehouse, supplier=self.supplier, processed=True)

        with self.assertRaises(ValueError) as ctx:
            process_inbound_document(doc.pk)
        self.assertIn('уже обработан', str(ctx.exception))


class OutboundProcessingTest(TestCase):
    """Тесты обработки расходного документа (фрагмент 15)."""

    def setUp(self):
        """Подготовка: наличие товара на складе."""
        self.warehouse = Warehouse.objects.create(
            name='Склад готовой продукции', type='finished')
        self.material = Material.objects.create(
            name='Шнурки', unit='pc', category='fittings', reorder_point=5)
        Stock.objects.create(
            warehouse=self.warehouse, material=self.material, quantity=10)

    def test_outbound_over_stock_fails(self):
        """Запрет списать больше, чем есть (фрагмент 15)."""
        doc = OutboundDocument.objects.create(
            doc_number='Р-001', doc_date='2026-07-02',
            warehouse=self.warehouse, purpose='production')
        OutboundItem.objects.create(
            outbound_doc=doc, material=self.material, quantity=20)

        with self.assertRaises(InsufficientStockError):
            process_outbound_document(doc.pk)

        # Остаток не должен измениться
        stock = Stock.objects.get(
            warehouse=self.warehouse, material=self.material)
        self.assertEqual(stock.quantity, 10)

    def test_outbound_success(self):
        """Успешный расход: остаток уменьшается."""
        doc = OutboundDocument.objects.create(
            doc_number='Р-002', doc_date='2026-07-03',
            warehouse=self.warehouse, purpose='production')
        OutboundItem.objects.create(
            outbound_doc=doc, material=self.material, quantity=3)

        process_outbound_document(doc.pk)

        stock = Stock.objects.get(
            warehouse=self.warehouse, material=self.material)
        self.assertEqual(stock.quantity, 7)

    def test_outbound_not_found(self):
        """Ошибка, если товара нет на этом складе."""
        other_warehouse = Warehouse.objects.create(
            name='Другой склад', type='raw')
        doc = OutboundDocument.objects.create(
            doc_number='Р-003', doc_date='2026-07-04',
            warehouse=other_warehouse, purpose='sale')
        OutboundItem.objects.create(
            outbound_doc=doc, material=self.material, quantity=1)

        with self.assertRaises(InsufficientStockError):
            process_outbound_document(doc.pk)


class MaterialAPITest(APITestCase):
    """Тесты REST API материалов (фрагмент 16)."""

    def setUp(self):
        """Создание пользователя и аутентификация."""
        self.user = User.objects.create_user(
            'kladovshik', password='testpass123')
        self.client.force_authenticate(self.user)

    def test_create_material(self):
        """Создание материала через API."""
        response = self.client.post('/api/materials/', {
            'name': 'Пуговицы',
            'unit': 'pc',
            'category': 'fittings',
            'reorder_point': 50
        })
        self.assertEqual(response.status_code, 201)

    def test_list_materials(self):
        """Список материалов доступен."""
        Material.objects.create(
            name='Тесьма', unit='m', category='fittings', reorder_point=10)
        response = self.client.get('/api/materials/')
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data['results']), 0)

    def test_unauthorized_denied(self):
        """Неаутентифицированный доступ запрещён (фрагмент 16).

        DRF при сессионной аутентификации возвращает 403 (доступ запрещён),
        а не 401 — код 401 отдаётся только при схемах с заголовком
        WWW-Authenticate (например, Basic/Token). Проверяем оба варианта.
        """
        self.client.force_authenticate(None)
        response = self.client.get('/api/materials/')
        self.assertIn(response.status_code, (401, 403))

    def test_low_stock_materials(self):
        """Экшн low_stock выдаёт материалы с остатком < минимума."""
        material = Material.objects.create(
            name='Резинка', unit='pc', category='fittings', reorder_point=100)
        warehouse = Warehouse.objects.create(name='Склад', type='raw')
        Stock.objects.create(
            warehouse=warehouse, material=material, quantity=5)

        response = self.client.get('/api/materials/low_stock/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(m['name'] == 'Резинка' for m in response.data))
