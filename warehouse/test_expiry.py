"""Проверка отчёта по срокам годности.

Срок годности и номер партии вводились при приходе, но нигде не
использовались. Эти тесты закрепляют, что теперь они работают — и что
отчёт честно ведёт себя в неочевидных случаях.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from warehouse.expiry import expiring_batches, expiry_summary
from warehouse.models import (InboundDocument, Material, Stock, Supplier,
                              Warehouse)
from warehouse.services import process_inbound_document

User = get_user_model()


class ExpiryBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'kladovshchik', password='x', role='storekeeper')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.warehouse = Warehouse.objects.create(name='Склад сырья',
                                                  type='raw')
        self.supplier = Supplier.objects.create(name='ООО «Химснаб»')
        self.glue = Material.objects.create(
            name='Клей полиуретановый', unit='l', category='polymer',
            reorder_point=Decimal('5'))
        self.today = timezone.localdate()

    def receive(self, material, quantity, expiry_days, batch='П-1',
                number=None, process=True):
        """Оформить и провести приход с заданным сроком годности."""
        document = InboundDocument.objects.create(
            doc_number=number or f'ПР-{material.id}-{expiry_days}-{batch}',
            doc_date=self.today, supplier=self.supplier,
            warehouse=self.warehouse)
        document.items.create(
            material=material, quantity=Decimal(quantity),
            unit_price=Decimal('100'), batch_number=batch,
            expiry_date=self.today + timedelta(days=expiry_days))
        if process:
            process_inbound_document(document.id)
        return document


class ExpiringBatchesTest(ExpiryBase):
    def test_expiring_soon_is_listed(self):
        self.receive(self.glue, 10, expiry_days=10)
        rows = expiring_batches(days=30)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['days_left'], 10)
        self.assertFalse(rows[0]['expired'])

    def test_already_expired_is_listed(self):
        self.receive(self.glue, 10, expiry_days=-5)
        rows = expiring_batches(days=30)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['expired'])
        self.assertEqual(rows[0]['days_left'], -5)

    def test_far_future_is_not_listed(self):
        self.receive(self.glue, 10, expiry_days=200)
        self.assertEqual(expiring_batches(days=30), [])

    def test_horizon_widens_the_list(self):
        self.receive(self.glue, 10, expiry_days=90)
        self.assertEqual(len(expiring_batches(days=30)), 0)
        self.assertEqual(len(expiring_batches(days=180)), 1)

    def test_unprocessed_document_ignored(self):
        """Непроведённый приход товара на склад не положил."""
        self.receive(self.glue, 10, expiry_days=5, process=False)
        self.assertEqual(expiring_batches(days=30), [])

    def test_without_expiry_date_ignored(self):
        """Материалы без срока годности в отчёт не попадают."""
        fabric = Material.objects.create(
            name='Ткань', unit='m', category='textile',
            reorder_point=Decimal('1'))
        document = InboundDocument.objects.create(
            doc_number='ПР-БЕЗ-СРОКА', doc_date=self.today,
            supplier=self.supplier, warehouse=self.warehouse)
        document.items.create(material=fabric, quantity=Decimal('50'),
                              unit_price=Decimal('10'))
        process_inbound_document(document.id)
        self.assertEqual(expiring_batches(days=30), [])

    def test_fully_consumed_position_hidden(self):
        """Просроченная партия израсходованного материала — не новость."""
        self.receive(self.glue, 10, expiry_days=-5)
        Stock.objects.filter(material=self.glue).update(quantity=0)
        self.assertEqual(expiring_batches(days=30), [])

    def test_sorted_by_date(self):
        self.receive(self.glue, 5, expiry_days=20, batch='Б')
        self.receive(self.glue, 5, expiry_days=-3, batch='А')
        self.receive(self.glue, 5, expiry_days=5, batch='В')
        rows = expiring_batches(days=30)
        self.assertEqual([r['batch_number'] for r in rows], ['А', 'В', 'Б'])

    def test_reported_fields(self):
        self.receive(self.glue, 12, expiry_days=7, batch='ПАРТИЯ-77')
        row = expiring_batches(days=30)[0]
        self.assertEqual(row['batch_number'], 'ПАРТИЯ-77')
        self.assertEqual(row['warehouse'], 'Склад сырья')
        self.assertEqual(row['supplier'], 'ООО «Химснаб»')
        self.assertEqual(row['quantity_received'], Decimal('12.00'))
        self.assertEqual(row['stock_remaining'], Decimal('12.00'))


class ExpirySummaryTest(ExpiryBase):
    def test_counts_split_by_state(self):
        self.receive(self.glue, 5, expiry_days=-1, batch='А')
        self.receive(self.glue, 5, expiry_days=-9, batch='Б')
        self.receive(self.glue, 5, expiry_days=10, batch='В')
        summary = expiry_summary(days=30)
        self.assertEqual(summary['expired_count'], 2)
        self.assertEqual(summary['expiring_count'], 1)
        self.assertEqual(summary['total_count'], 3)

    def test_limits_items_to_five(self):
        for i in range(8):
            self.receive(self.glue, 2, expiry_days=i, batch=f'П{i}')
        self.assertEqual(len(expiry_summary()['items']), 5)


class ExpiryApiTest(ExpiryBase):
    def test_report_returns_rows(self):
        self.receive(self.glue, 10, expiry_days=3, batch='ПАРТИЯ-1')
        response = self.client.get('/api/reports/expiry/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['истекает'], 1)
        self.assertEqual(response.data['строки'][0]['партия'], 'ПАРТИЯ-1')

    def test_days_parameter(self):
        self.receive(self.glue, 10, expiry_days=90)
        self.assertEqual(
            self.client.get('/api/reports/expiry/').data['истекает'], 0)
        self.assertEqual(
            self.client.get('/api/reports/expiry/?days=120').data['истекает'],
            1)

    def test_bad_days_parameter(self):
        response = self.client.get('/api/reports/expiry/?days=скоро')
        self.assertEqual(response.status_code, 400)

    def test_requires_authentication(self):
        response = APIClient().get('/api/reports/expiry/')
        self.assertIn(response.status_code, (401, 403))

    def test_note_about_batch_accounting(self):
        """Отчёт должен честно предупреждать об ограничении."""
        response = self.client.get('/api/reports/expiry/')
        self.assertIn('не по партиям', response.data['примечание'])

    def test_dashboard_includes_expiry(self):
        self.receive(self.glue, 10, expiry_days=-2, batch='ПРОСРОК')
        response = self.client.get('/api/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['expiry']['expired_count'], 1)
        self.assertEqual(response.data['expiry']['items'][0]['batch'],
                         'ПРОСРОК')
