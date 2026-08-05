"""
API управления заказами покупателей (для сотрудников склада).

Покупатели оформляют заказы через публичную витрину, а здесь сотрудники
их обрабатывают: подтверждают, отгружают, отменяют. Доступ только для
авторизованных пользователей.
"""
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Order, OrderItem
from .order_services import (cancel_order, confirm_order, ship_order)
from .services import InsufficientStockError


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(
        source='product.name', read_only=True)
    article = serializers.CharField(
        source='product.article_number', read_only=True)
    size = serializers.CharField(source='product.size', read_only=True)
    color = serializers.CharField(source='product.color', read_only=True)
    sum = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'product_name', 'article', 'size',
                  'color', 'quantity', 'price', 'sum']


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    status_display = serializers.CharField(
        source='get_status_display', read_only=True)
    total = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Order
        fields = ['id', 'number', 'customer_name', 'customer_phone',
                  'customer_email', 'address', 'comment', 'status',
                  'status_display', 'created_at', 'items', 'total',
                  'outbound_document']
        read_only_fields = ['number', 'created_at', 'outbound_document']


class OrderViewSet(viewsets.ModelViewSet):
    """Заказы покупателей. Создаются через витрину, обрабатываются здесь."""

    queryset = (Order.objects
                .prefetch_related('items__product')
                .select_related('outbound_document'))
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['status']
    ordering_fields = ['created_at', 'number']

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """Подтвердить заказ (связались с покупателем)."""
        try:
            order = confirm_order(pk)
            return Response({'status': 'ok',
                             'detail': f'Заказ {order.number} подтверждён'})
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def ship(self, request, pk=None):
        """Отгрузить заказ: списать товар со склада."""
        warehouse_id = request.data.get('warehouse')
        if not warehouse_id:
            return Response({'error': 'Укажите склад отгрузки'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            order = ship_order(pk, warehouse_id, user=request.user)
            return Response({
                'status': 'ok',
                'detail': f'Заказ {order.number} отгружен, товар списан'})
        except InsufficientStockError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Отменить заказ. Резерв снимается автоматически."""
        try:
            order = cancel_order(pk)
            return Response({'status': 'ok',
                             'detail': f'Заказ {order.number} отменён'})
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
