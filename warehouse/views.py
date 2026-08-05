"""
Обработчики REST API (фрагмент 12 отчёта).

Реализованы в виде ViewSet-классов со стандартными операциями CRUD и
дополнительными действиями: список материалов с остатком ниже минимального,
проведение приходного/расходного документа. Доступ ко всем методам требует
аутентификации. Для списков документов применён select_related — устранение
проблемы N+1 запросов (фрагмент 17).
"""
from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (InboundDocument, Material, OutboundDocument, Product,
                     Stock, StockMovement, Supplier, Warehouse)
from .serializers import (InboundDocumentSerializer, MaterialSerializer,
                          OutboundDocumentSerializer, ProductSerializer,
                          StockMovementSerializer, StockSerializer,
                          SupplierSerializer, WarehouseSerializer)
from .services import (InsufficientStockError, process_inbound_document,
                       process_outbound_document, produce_product,
                       unprocess_inbound_document,
                       unprocess_outbound_document)


class MaterialViewSet(viewsets.ModelViewSet):
    queryset = Material.objects.all()
    serializer_class = MaterialSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['category', 'unit']
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'category', 'reorder_point']

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        """Материалы, чей суммарный остаток ниже минимального."""
        qs = (Material.objects
              .annotate(total=Coalesce(
                  Sum('stock__quantity'),
                  Value(0, output_field=DecimalField())))
              .filter(total__lt=F('reorder_point')))
        data = [{
            **MaterialSerializer(m).data,
            'current_stock': m.total,
        } for m in qs]
        return Response(data)


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['category', 'size', 'color', 'status']
    search_fields = ['name', 'article_number', 'color']
    ordering_fields = ['name', 'article_number', 'selling_price']


class WarehouseViewSet(viewsets.ModelViewSet):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer
    permission_classes = [IsAuthenticated]


class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ['name', 'inn', 'contact_person']


class InboundDocumentViewSet(viewsets.ModelViewSet):
    # select_related — один запрос с JOIN вместо N+1 (фрагмент 17)
    queryset = (InboundDocument.objects
                .select_related('supplier', 'warehouse', 'created_by')
                .prefetch_related('items'))
    serializer_class = InboundDocumentSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['warehouse', 'supplier', 'processed']
    ordering_fields = ['doc_date', 'doc_number']

    @action(detail=True, methods=['post'])
    def process(self, request, pk=None):
        """Провести приходный документ и обновить остатки."""
        try:
            process_inbound_document(pk, user=request.user)
            return Response({'status': 'ok',
                             'detail': 'Документ проведён'})
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        """Удалять можно только непроведённые документы.

        Проведённый документ уже изменил остатки на складе. Его удаление
        оставило бы товар на складе без документа-основания и нарушило бы
        целостность учёта.
        """
        doc = self.get_object()
        if doc.processed:
            return Response(
                {'error': 'Нельзя удалить проведённый документ. '
                          'Сначала отмените проведение (сторно).'},
                status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """Проведённый документ нельзя редактировать."""
        doc = self.get_object()
        if doc.processed:
            return Response(
                {'error': 'Нельзя изменить проведённый документ. '
                          'Сначала отмените проведение (сторно).'},
                status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def unprocess(self, request, pk=None):
        """Отменить проведение документа (сторно): откатить остатки."""
        try:
            unprocess_inbound_document(pk, user=request.user)
            return Response({'status': 'ok',
                             'detail': 'Проведение отменено, остатки откачены'})
        except InsufficientStockError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)


class OutboundDocumentViewSet(viewsets.ModelViewSet):
    queryset = (OutboundDocument.objects
                .select_related('warehouse', 'created_by')
                .prefetch_related('items'))
    serializer_class = OutboundDocumentSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['warehouse', 'purpose', 'processed']
    ordering_fields = ['doc_date', 'doc_number']

    @action(detail=True, methods=['post'])
    def process(self, request, pk=None):
        """Провести расходный документ с проверкой остатков."""
        try:
            process_outbound_document(pk, user=request.user)
            return Response({'status': 'ok',
                             'detail': 'Документ проведён'})
        except InsufficientStockError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        """Удалять можно только непроведённые документы."""
        doc = self.get_object()
        if doc.processed:
            return Response(
                {'error': 'Нельзя удалить проведённый документ. '
                          'Сначала отмените проведение (сторно).'},
                status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """Проведённый документ нельзя редактировать."""
        doc = self.get_object()
        if doc.processed:
            return Response(
                {'error': 'Нельзя изменить проведённый документ. '
                          'Сначала отмените проведение (сторно).'},
                status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def unprocess(self, request, pk=None):
        """Отменить проведение расхода: вернуть товар на склад."""
        try:
            unprocess_outbound_document(pk, user=request.user)
            return Response({'status': 'ok',
                             'detail': 'Проведение отменено, товар возвращён'})
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)


class StockViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (Stock.objects
                .select_related('warehouse', 'material', 'product')
                .filter(quantity__gt=0))
    serializer_class = StockSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['warehouse', 'material', 'product']


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (StockMovement.objects
                .select_related('warehouse', 'material', 'product', 'user'))
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['warehouse', 'movement_type', 'material', 'product']
    ordering_fields = ['created_at']


# --- Сводка для главной панели ----------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    """Данные для главной панели: стоимость запасов, дефицит, операции."""
    money = DecimalField(max_digits=16, decimal_places=2)

    # Стоимость материалов = остаток × средняя себестоимость недоступна,
    # поэтому берём по последней закупочной цене из истории. Для простоты
    # оцениваем продукцию по себестоимости, материалы — по reorder-независимо.
    materials_value = (Stock.objects
                       .filter(material__isnull=False)
                       .aggregate(v=Coalesce(
                           Sum(F('quantity')), Value(0),
                           output_field=money))['v'])

    products_value = (Stock.objects
                      .filter(product__isnull=False)
                      .aggregate(v=Coalesce(
                          Sum(F('quantity') * F('product__cost')),
                          Value(0), output_field=money))['v'])

    low_stock_count = 0
    low_items = []
    for m in Material.objects.annotate(
            total=Coalesce(Sum('stock__quantity'),
                           Value(0, output_field=money))):
        if m.total < m.reorder_point:
            low_stock_count += 1
            low_items.append({
                'name': m.name,
                'current': float(m.total),
                'reorder_point': float(m.reorder_point),
                'unit': m.get_unit_display(),
            })

    recent = StockMovement.objects.select_related(
        'warehouse', 'material', 'product', 'user')[:10]

    # Динамика движения — для графика на главной. Показывает, в какие дни
    # склад принимал, а в какие отгружал: по этой картине сразу виден ритм
    # работы и провалы в поставках.
    from datetime import timedelta
    from django.utils import timezone as dj_timezone

    try:
        period = int(request.query_params.get('days', 14))
    except (TypeError, ValueError):
        period = 14
    period = max(7, min(period, 90))   # разумные границы

    today = dj_timezone.localdate()
    start = today - timedelta(days=period - 1)

    daily = {}
    movements = (StockMovement.objects
                 .filter(created_at__date__gte=start)
                 .values('created_at__date', 'movement_type')
                 .annotate(total=Coalesce(Sum('quantity'),
                                          Value(0, output_field=money))))
    for row in movements:
        day = row['created_at__date']
        bucket = daily.setdefault(day, {'in': 0.0, 'out': 0.0})
        if row['movement_type'] == 'in':
            bucket['in'] += float(row['total'])
        elif row['movement_type'] == 'out':
            bucket['out'] += float(row['total'])

    chart = []
    for offset in range(period):
        day = start + timedelta(days=offset)
        bucket = daily.get(day, {'in': 0.0, 'out': 0.0})
        chart.append({
            'date': day.strftime('%d.%m'),
            'full_date': day.strftime('%d.%m.%Y'),
            'in': round(bucket['in'], 2),
            'out': round(bucket['out'], 2),
        })

    return Response({
        'materials_positions': Stock.objects.filter(
            material__isnull=False, quantity__gt=0).count(),
        'products_positions': Stock.objects.filter(
            product__isnull=False, quantity__gt=0).count(),
        'products_value': float(products_value or 0),
        'materials_total_qty': float(materials_value or 0),
        'low_stock_count': low_stock_count,
        'low_stock_items': low_items[:10],
        'recent_movements': StockMovementSerializer(recent, many=True).data,
        'chart': chart,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def produce_view(request):
    """Произвести продукцию из материалов.

    Ожидает JSON:
    {
        "product": 1,
        "quantity": 10,
        "product_warehouse": 2,
        "material_warehouse": 1,
        "materials": [{"material": 5, "quantity": 15}, ...]
    }
    """
    data = request.data
    try:
        product = produce_product(
            product_id=data['product'],
            quantity=data['quantity'],
            product_warehouse_id=data['product_warehouse'],
            materials=data.get('materials', []),
            material_warehouse_id=data['material_warehouse'],
            user=request.user if request.user.is_authenticated else None)
        return Response({'status': 'ok', 'product': product.name,
                         'quantity': str(data['quantity'])})
    except InsufficientStockError as e:
        return Response({'error': str(e)},
                        status=status.HTTP_400_BAD_REQUEST)
    except (KeyError, ValueError) as e:
        return Response({'error': f'Некорректные данные: {e}'},
                        status=status.HTTP_400_BAD_REQUEST)
