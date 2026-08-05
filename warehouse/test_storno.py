"""Тесты новой логики: сторно и защита проведённых документов."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from warehouse.models import (InboundDocument, InboundItem, Material,
                              OutboundDocument, OutboundItem, Stock,
                              Supplier, Warehouse)
from warehouse.services import (InsufficientStockError,
                                process_inbound_document,
                                process_outbound_document,
                                unprocess_inbound_document,
                                unprocess_outbound_document)

User = get_user_model()


class StornoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('t', password='x')
        self.wh = Warehouse.objects.create(name='Склад', type='raw')
        self.sup = Supplier.objects.create(name='Поставщик')
        self.mat = Material.objects.create(
            name='Кожа', unit='m', category='leather', reorder_point=10)

    def _make_inbound(self, qty):
        doc = InboundDocument.objects.create(
            doc_number=f'П-{qty}', doc_date='2026-01-01',
            supplier=self.sup, warehouse=self.wh)
        InboundItem.objects.create(
            inbound_doc=doc, material=self.mat, quantity=qty, unit_price=100)
        return doc

    def test_unprocess_inbound_rolls_back(self):
        """Отмена прихода списывает оприходованное обратно."""
        doc = self._make_inbound(50)
        process_inbound_document(doc.id, self.user)
        self.assertEqual(Stock.objects.get(warehouse=self.wh, material=self.mat).quantity, 50)

        unprocess_inbound_document(doc.id, self.user)
        self.assertEqual(Stock.objects.get(warehouse=self.wh, material=self.mat).quantity, 0)
        doc.refresh_from_db()
        self.assertFalse(doc.processed)

    def test_unprocess_blocked_if_consumed(self):
        """Нельзя отменить приход, если товар уже израсходован."""
        doc = self._make_inbound(50)
        process_inbound_document(doc.id, self.user)

        # Расходуем 30 из 50
        out = OutboundDocument.objects.create(
            doc_number='Р-1', doc_date='2026-01-02',
            warehouse=self.wh, purpose='sale')
        OutboundItem.objects.create(
            outbound_doc=out, material=self.mat, quantity=30, unit_price=0)
        process_outbound_document(out.id, self.user)
        self.assertEqual(Stock.objects.get(warehouse=self.wh, material=self.mat).quantity, 20)

        # Откатить приход на 50 нельзя — на складе только 20
        with self.assertRaises(InsufficientStockError):
            unprocess_inbound_document(doc.id, self.user)

    def test_unprocess_outbound_returns_stock(self):
        """Отмена расхода возвращает товар на склад."""
        doc = self._make_inbound(100)
        process_inbound_document(doc.id, self.user)

        out = OutboundDocument.objects.create(
            doc_number='Р-2', doc_date='2026-01-02',
            warehouse=self.wh, purpose='sale')
        OutboundItem.objects.create(
            outbound_doc=out, material=self.mat, quantity=40, unit_price=0)
        process_outbound_document(out.id, self.user)
        self.assertEqual(Stock.objects.get(warehouse=self.wh, material=self.mat).quantity, 60)

        unprocess_outbound_document(out.id, self.user)
        self.assertEqual(Stock.objects.get(warehouse=self.wh, material=self.mat).quantity, 100)
        out.refresh_from_db()
        self.assertFalse(out.processed)

    def test_unprocess_unprocessed_fails(self):
        """Нельзя отменить непроведённый документ."""
        doc = self._make_inbound(10)
        with self.assertRaises(ValueError):
            unprocess_inbound_document(doc.id, self.user)
