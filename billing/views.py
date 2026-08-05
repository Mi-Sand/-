"""
Выгрузка документов для бухгалтерии.

Все обработчики отдают готовый файл Excel. Возвращается именно
HttpResponse, а не Response из DRF: тот пытается разобрать содержимое как
текст и на двоичных данных книги Excel завершается ошибкой.
"""
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

from django.db.models import Q, Sum
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from warehouse.models import Order

from .documents.invoice import build_invoice
from .documents.reconciliation import build_reconciliation, collect_movements
from .documents.torg12 import build_torg12
from .documents.upd import build_upd
from .models import CompanyRequisites, Counterparty, Payment
from .serializers import CounterpartySerializer, PaymentSerializer

XLSX_CONTENT_TYPE = ('application/vnd.openxmlformats-officedocument'
                     '.spreadsheetml.sheet')


def _xlsx_response(stream, filename):
    """Отдать книгу Excel файлом с корректным русским именем.

    Имя записывается дважды: латиницей в filename для старых программ и
    в кодировке из RFC 5987 в filename* — иначе кириллица в названии
    файла превращается у покупателя в набор знаков.
    """
    response = HttpResponse(stream.read(), content_type=XLSX_CONTENT_TYPE)
    ascii_name = 'document.xlsx'
    response['Content-Disposition'] = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}")
    return response


def _get_requisites_or_error():
    """Реквизиты организации либо понятный отказ."""
    requisites = CompanyRequisites.get_active()
    if requisites is None:
        return None, Response(
            {'error': 'Не заполнены реквизиты организации. Без них '
                      'документы выставить нельзя. Откройте админку, '
                      'раздел «Реквизиты организации», и заполните их.'},
            status=status.HTTP_400_BAD_REQUEST)
    return requisites, None


def _get_order(pk):
    return (Order.objects
            .prefetch_related('items__product')
            .select_related('outbound_document', 'counterparty')
            .filter(pk=pk)
            .first())


def _order_document(request, pk, builder, filename_template):
    """Общая часть выгрузки документа по заказу."""
    requisites, error = _get_requisites_or_error()
    if error:
        return error

    order = _get_order(pk)
    if order is None:
        return Response({'error': 'Заказ не найден'},
                        status=status.HTTP_404_NOT_FOUND)
    if not order.items.exists():
        return Response({'error': 'В заказе нет позиций'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        stream = builder(order, requisites)
    except ValueError as e:
        return Response({'error': str(e)},
                        status=status.HTTP_400_BAD_REQUEST)

    return _xlsx_response(stream, filename_template.format(order.number))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def order_invoice(request, pk):
    """Счёт на оплату по заказу."""
    return _order_document(request, pk, build_invoice, 'Счёт {}.xlsx')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def order_torg12(request, pk):
    """Товарная накладная ТОРГ-12 по заказу."""
    return _order_document(request, pk, build_torg12, 'Накладная {}.xlsx')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def order_upd(request, pk):
    """Универсальный передаточный документ по заказу."""
    return _order_document(request, pk, build_upd, 'УПД {}.xlsx')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reconciliation_report(request):
    """Акт сверки с покупателем за период.

    Параметры: counterparty — организация из справочника; from и to —
    границы периода в виде ГГГГ-ММ-ДД.
    """
    requisites, error = _get_requisites_or_error()
    if error:
        return error

    counterparty_id = request.query_params.get('counterparty')
    if not counterparty_id:
        return Response({'error': 'Укажите покупателя'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        counterparty = Counterparty.objects.get(pk=int(counterparty_id))
    except (Counterparty.DoesNotExist, TypeError, ValueError):
        return Response({'error': 'Покупатель не найден'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        period_start = _parse_date(request.query_params.get('from'),
                                   date.today() - timedelta(days=365))
        period_end = _parse_date(request.query_params.get('to'),
                                 date.today())
    except ValueError as e:
        return Response({'error': str(e)},
                        status=status.HTTP_400_BAD_REQUEST)

    if period_start > period_end:
        return Response(
            {'error': 'Начало периода позже его конца'},
            status=status.HTTP_400_BAD_REQUEST)

    orders_all = (Order.objects
                  .filter(counterparty=counterparty)
                  .exclude(status='cancelled')
                  .select_related('outbound_document')
                  .prefetch_related('items'))

    # В акт попадают только отгруженные заказы: до отгрузки обязательство
    # ещё не возникло, и включать такой заказ в расчёты рано.
    shipped = orders_all.filter(status='shipped')

    in_period = [
        order for order in shipped
        if period_start <= _order_date(order) <= period_end]
    before_period = [
        order for order in shipped if _order_date(order) < period_start]

    payments = Payment.objects.filter(order__counterparty=counterparty)
    payments_in_period = payments.filter(paid_at__gte=period_start,
                                         paid_at__lte=period_end)
    payments_before = payments.filter(paid_at__lt=period_start)

    # Сальдо на начало: всё, что отгружено и оплачено до периода
    opening = (sum((Decimal(order.total) for order in before_period),
                   Decimal('0.00'))
               - (payments_before.aggregate(total=Sum('amount'))['total']
                  or Decimal('0.00')))

    movements = collect_movements(in_period, payments_in_period)

    stream = build_reconciliation(
        counterparty.short_name, movements, period_start, period_end,
        requisites, opening_balance=opening)

    return _xlsx_response(
        stream, f'Акт сверки {counterparty.short_name}.xlsx')


def _order_date(order):
    document = order.outbound_document
    return document.doc_date if document else order.created_at.date()


def _parse_date(value, default):
    if value in (None, ''):
        return default
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f'Неверная дата: {value!r}. Ожидается ГГГГ-ММ-ДД')


class CounterpartyViewSet(viewsets.ModelViewSet):
    """Справочник покупателей-организаций."""

    queryset = Counterparty.objects.all()
    serializer_class = CounterpartySerializer
    permission_classes = [IsAuthenticated]
    search_fields = ['short_name', 'inn']
    ordering_fields = ['short_name', 'created_at']


class PaymentViewSet(viewsets.ModelViewSet):
    """Поступившие оплаты. Нужны для акта сверки."""

    queryset = (Payment.objects
                .select_related('order', 'order__counterparty', 'created_by'))
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['order', 'method']
    ordering_fields = ['paid_at', 'amount']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
