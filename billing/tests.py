"""Проверка документов для бухгалтерии.

Проверяется не только то, что файл собрался, но и что в нём стоят верные
суммы: ошибка в бланке обнаруживается позже всех — обычно уже у
покупателя или при налоговой проверке.
"""
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

import openpyxl
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.calc import buyer_details, order_document_data
from billing.documents.invoice import build_invoice
from billing.documents.reconciliation import (build_reconciliation,
                                              collect_movements)
from billing.documents.torg12 import build_torg12
from billing.documents.upd import build_upd
from billing.models import CompanyRequisites, Counterparty, Payment
from warehouse.models import (Order, OrderItem, OutboundDocument, Product,
                              Stock, Warehouse)

User = get_user_model()


def cell_texts(stream):
    """Все непустые значения книги строкой — для поиска по содержимому."""
    stream.seek(0)
    workbook = openpyxl.load_workbook(BytesIO(stream.read()))
    sheet = workbook.active
    values = []
    for row in sheet.iter_rows():
        for cell in row:
            if cell.value not in (None, ''):
                values.append(str(cell.value))
    return values


class BillingBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'buhgalter', password='x', role='admin', is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.requisites = CompanyRequisites.objects.create(
            short_name='ООО «ЛЕКО»',
            full_name='Общество с ограниченной ответственностью «ЛЕКО»',
            inn='5007012345', kpp='500701001',
            legal_address='г. Дмитров, ул. Заводская, д. 1',
            bank_name='ПАО СБЕРБАНК', bank_bik='044525225',
            settlement_account='40702810100000012345',
            correspondent_account='30101810400000000225',
            director_name='Иванов И. И.', accountant_name='Петрова А. С.',
            vat_rate='20', vat_included_in_price=True)

        self.warehouse = Warehouse.objects.create(name='Основной',
                                                  type='finished')
        self.product = Product.objects.create(
            article_number='A-1', name='Ботинки', category='shoes',
            size='42', color='чёрный', cost=100, selling_price=Decimal('1200'))
        Stock.objects.create(warehouse=self.warehouse, product=self.product,
                             quantity=100)

        self.order = Order.objects.create(
            number='ЗАК-00001', customer_name='Иван Петров',
            customer_phone='+79991234567',
            address='г. Дмитров, ул. Советская, д. 10')
        OrderItem.objects.create(order=self.order, product=self.product,
                                 quantity=2, price=Decimal('1200'))


class VatSplitTest(BillingBase):
    def test_line_amounts(self):
        """2 × 1200 = 2400, из них НДС 20/120 = 400."""
        lines, totals = order_document_data(self.order, self.requisites)
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line.total, Decimal('2400.00'))
        self.assertEqual(line.vat, Decimal('400.00'))
        self.assertEqual(line.net, Decimal('2000.00'))
        self.assertEqual(line.price_without_vat, Decimal('1000.00'))

    def test_totals_match_lines(self):
        lines, totals = order_document_data(self.order, self.requisites)
        self.assertEqual(totals.net + totals.vat, totals.total)
        self.assertEqual(totals.total, Decimal('2400.00'))

    def test_vat_added_on_top_when_configured(self):
        """Если цены без НДС, налог начисляется сверх, а не выделяется."""
        self.requisites.vat_included_in_price = False
        self.requisites.save()
        lines, totals = order_document_data(self.order, self.requisites)
        self.assertEqual(totals.net, Decimal('2400.00'))
        self.assertEqual(totals.vat, Decimal('480.00'))
        self.assertEqual(totals.total, Decimal('2880.00'))

    def test_without_vat(self):
        self.requisites.vat_rate = 'none'
        self.requisites.save()
        lines, totals = order_document_data(self.order, self.requisites)
        self.assertEqual(totals.vat, Decimal('0.00'))
        self.assertEqual(totals.net, totals.total)


class BuyerDetailsTest(BillingBase):
    def test_private_person(self):
        buyer = buyer_details(self.order)
        self.assertFalse(buyer['is_organization'])
        self.assertEqual(buyer['name'], 'Иван Петров')
        self.assertEqual(buyer['requisites'], '')

    def test_organization(self):
        party = Counterparty.objects.create(
            short_name='ООО «Ромашка»', inn='7701234567', kpp='770101001',
            legal_address='г. Москва, ул. Мира, 5')
        self.order.counterparty = party
        self.order.save()

        buyer = buyer_details(self.order)
        self.assertTrue(buyer['is_organization'])
        self.assertEqual(buyer['name'], 'ООО «Ромашка»')
        self.assertIn('ИНН 7701234567', buyer['requisites'])
        self.assertIn('КПП 770101001', buyer['requisites'])


class InvoiceTest(BillingBase):
    def test_contains_key_requisites(self):
        """В счёте должно быть всё, по чему покупатель платит."""
        texts = cell_texts(build_invoice(self.order, self.requisites))
        joined = ' | '.join(texts)

        self.assertIn('40702810100000012345', joined)   # расчётный счёт
        self.assertIn('044525225', joined)              # БИК
        self.assertIn('ПАО СБЕРБАНК', joined)
        self.assertIn('ЗАК-00001', joined)
        self.assertIn('Иван Петров', joined)
        self.assertIn('Иванов И. И.', joined)

    def test_amount_in_words(self):
        texts = cell_texts(build_invoice(self.order, self.requisites))
        self.assertIn('Две тысячи четыреста рублей 00 копеек', texts)

    def test_vat_line_present(self):
        joined = ' | '.join(cell_texts(
            build_invoice(self.order, self.requisites)))
        self.assertIn('В том числе НДС 20%:', joined)

    def test_without_requisites_refuses(self):
        with self.assertRaises(ValueError) as context:
            build_invoice(self.order, None)
        self.assertIn('реквизиты', str(context.exception).lower())


class Torg12Test(BillingBase):
    def test_builds_and_has_totals(self):
        texts = cell_texts(build_torg12(self.order, self.requisites))
        joined = ' | '.join(texts)
        self.assertIn('ТОВАРНАЯ НАКЛАДНАЯ № ЗАК-00001', joined)
        self.assertIn('Две тысячи четыреста рублей 00 копеек', texts)

    def test_record_count_agrees_in_number(self):
        """«1 (один) порядковый номер записи», а не «номеров записей»."""
        joined = ' | '.join(cell_texts(
            build_torg12(self.order, self.requisites)))
        self.assertIn('1 (один) порядковый номер записи', joined)

    def test_uses_shipment_date_when_shipped(self):
        document = OutboundDocument.objects.create(
            doc_number='ОТГ-1', doc_date=date(2026, 3, 15),
            warehouse=self.warehouse, purpose='sale')
        self.order.outbound_document = document
        self.order.save()
        joined = ' | '.join(cell_texts(
            build_torg12(self.order, self.requisites)))
        self.assertIn('15.03.2026', joined)


class UpdTest(BillingBase):
    def test_status_one_with_vat(self):
        """С НДС документ заменяет и счёт-фактуру — статус 1."""
        joined = ' | '.join(cell_texts(
            build_upd(self.order, self.requisites)))
        self.assertIn('1 — счёт-фактура и передаточный документ', joined)
        self.assertIn('Счёт-фактура № ЗАК-00001', joined)

    def test_status_two_without_vat(self):
        """Без НДС счёт-фактуру выписывать не с чего — статус 2."""
        self.requisites.vat_rate = 'none'
        self.requisites.save()
        joined = ' | '.join(cell_texts(
            build_upd(self.order, self.requisites)))
        self.assertIn('2 — передаточный документ', joined)
        self.assertNotIn('Счёт-фактура №', joined)


class ReconciliationTest(BillingBase):
    def setUp(self):
        super().setUp()
        self.party = Counterparty.objects.create(
            short_name='ООО «Ромашка»', inn='7701234567', kpp='770101001')
        self.order.counterparty = self.party
        self.order.status = 'shipped'
        self.order.save()

    def test_balance_arithmetic(self):
        """Сальдо конечное = начальное + отгрузки − оплаты."""
        Payment.objects.create(order=self.order, amount=Decimal('1000.00'),
                               paid_at=date(2026, 5, 1))
        movements = collect_movements(
            [self.order], Payment.objects.filter(order=self.order))

        stream = build_reconciliation(
            'ООО «Ромашка»', movements, date(2026, 1, 1), date(2026, 12, 31),
            self.requisites, opening_balance=Decimal('500.00'))

        texts = cell_texts(stream)
        joined = ' | '.join(texts)
        # 500 + 2400 − 1000 = 1900
        self.assertIn('Одна тысяча девятьсот рублей 00 копеек', joined)
        self.assertIn('задолженность ООО «Ромашка»', joined)

    def test_no_debt_wording(self):
        Payment.objects.create(order=self.order, amount=Decimal('2400.00'),
                               paid_at=date(2026, 5, 1))
        movements = collect_movements(
            [self.order], Payment.objects.filter(order=self.order))
        joined = ' | '.join(cell_texts(build_reconciliation(
            'ООО «Ромашка»', movements, date(2026, 1, 1),
            date(2026, 12, 31), self.requisites)))
        self.assertIn('задолженность сторон друг перед другом отсутствует',
                      joined)

    def test_movements_sorted_by_date(self):
        Payment.objects.create(order=self.order, amount=Decimal('100.00'),
                               paid_at=date(2020, 1, 1))
        movements = collect_movements(
            [self.order], Payment.objects.filter(order=self.order))
        dates = [item['date'] for item in movements]
        self.assertEqual(dates, sorted(dates))


class DocumentApiTest(BillingBase):
    def test_all_three_documents_download(self):
        for kind in ('invoice', 'torg12', 'upd'):
            with self.subTest(kind=kind):
                response = self.client.get(f'/api/orders/{self.order.id}/{kind}/')
                self.assertEqual(response.status_code, 200)
                self.assertIn('spreadsheetml', response['Content-Type'])
                self.assertIn('attachment', response['Content-Disposition'])

    def test_requires_authentication(self):
        anonymous = APIClient()
        response = anonymous.get(f'/api/orders/{self.order.id}/invoice/')
        self.assertIn(response.status_code, (401, 403))

    def test_missing_order(self):
        response = self.client.get('/api/orders/99999/invoice/')
        self.assertEqual(response.status_code, 404)

    def test_without_requisites_explains(self):
        CompanyRequisites.objects.all().delete()
        response = self.client.get(f'/api/orders/{self.order.id}/invoice/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('реквизиты', response.data['error'].lower())

    def test_empty_order_refused(self):
        empty = Order.objects.create(
            number='ЗАК-00002', customer_name='Пустой Заказ',
            customer_phone='+79990000000')
        response = self.client.get(f'/api/orders/{empty.id}/invoice/')
        self.assertEqual(response.status_code, 400)

    def test_reconciliation_requires_counterparty(self):
        response = self.client.get('/api/reconciliation/')
        self.assertEqual(response.status_code, 400)

    def test_reconciliation_bad_date(self):
        party = Counterparty.objects.create(
            short_name='ООО «Тест»', inn='7709999999')
        response = self.client.get(
            f'/api/reconciliation/?counterparty={party.id}&from=не-дата')
        self.assertEqual(response.status_code, 400)

    def test_reconciliation_reversed_period(self):
        party = Counterparty.objects.create(
            short_name='ООО «Тест»', inn='7709999999')
        response = self.client.get(
            f'/api/reconciliation/?counterparty={party.id}'
            f'&from=2026-12-31&to=2026-01-01')
        self.assertEqual(response.status_code, 400)

    def test_reconciliation_downloads(self):
        party = Counterparty.objects.create(
            short_name='ООО «Ромашка»', inn='7701234567', kpp='770101001')
        self.order.counterparty = party
        self.order.status = 'shipped'
        self.order.save()
        response = self.client.get(
            f'/api/reconciliation/?counterparty={party.id}'
            f'&from=2026-01-01&to=2026-12-31')
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])


class PaymentApiTest(BillingBase):
    def test_create_payment(self):
        response = self.client.post('/api/payments/', {
            'order': self.order.id, 'amount': '1200.00',
            'paid_at': '2026-05-01', 'method': 'bank',
            'document_number': '42'}, format='json')
        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get(pk=response.data['id'])
        self.assertEqual(payment.created_by, self.user)

    def test_negative_amount_refused(self):
        response = self.client.post('/api/payments/', {
            'order': self.order.id, 'amount': '-100',
            'paid_at': '2026-05-01'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_payment_on_cancelled_order_refused(self):
        self.order.status = 'cancelled'
        self.order.save()
        response = self.client.post('/api/payments/', {
            'order': self.order.id, 'amount': '100',
            'paid_at': '2026-05-01'}, format='json')
        self.assertEqual(response.status_code, 400)


class CounterpartyApiTest(BillingBase):
    def test_duplicate_gives_readable_error(self):
        """Повтор ИНН и КПП — понятное сообщение, а не ошибка сервера."""
        Counterparty.objects.create(short_name='ООО «Ромашка»',
                                    inn='7701234567', kpp='770101001')
        response = self.client.post('/api/counterparties/', {
            'short_name': 'ООО «Ромашка» (дубль)',
            'inn': '7701234567', 'kpp': '770101001'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('уже заведён', str(response.data))

    def test_same_inn_other_kpp_allowed(self):
        """Обособленные подразделения: ИНН общий, КПП разный."""
        Counterparty.objects.create(short_name='ООО «Ромашка»',
                                    inn='7701234567', kpp='770101001')
        response = self.client.post('/api/counterparties/', {
            'short_name': 'ООО «Ромашка», филиал',
            'inn': '7701234567', 'kpp': '500101001'}, format='json')
        self.assertEqual(response.status_code, 201)


class CompanyRequisitesTest(BillingBase):
    def test_only_one_active_record(self):
        """Вторые реквизиты завести нельзя — документы печатались бы разными."""
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CompanyRequisites.objects.create(
                    short_name='ООО «Второе»', full_name='ООО «Второе»',
                    inn='7700000000', legal_address='адрес',
                    bank_name='банк', bank_bik='044525225',
                    settlement_account='40702810100000000000',
                    director_name='Сидоров С. С.')

    def test_vat_percent(self):
        self.assertEqual(self.requisites.vat_percent, Decimal('20'))
        self.requisites.vat_rate = 'none'
        self.assertIsNone(self.requisites.vat_percent)
        self.assertEqual(self.requisites.vat_display, 'Без налога (НДС)')
