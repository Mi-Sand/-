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

from accounts.permissions import CanEditDocuments, CanProcessOrders

from .models import Order, OrderItem
from .order_services import (cancel_order, confirm_order, create_order,
                             ship_order)
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

    def create(self, request, *args, **kwargs):
        """Завести заказ из закрытой части — например, по телефону.

        Раньше сюда попадала обычная запись модели: заказ сохранялся
        без номера и без единой позиции, потому что номер и строки
        назначаются не здесь, а при оформлении. В списке появлялась
        карточка «Итого: 0», которую нельзя ни отгрузить, ни понять.

        Теперь заказ заводится тем же путём, что и с витрины: с
        проверкой наличия, резервированием товара и присвоением номера.
        """
        data = request.data
        items = data.get('items')
        if not isinstance(items, list) or not items:
            return Response(
                {'error': 'В заказе нет ни одной позиции. Укажите items: '
                          '[{"product": 1, "quantity": 2}].'},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            order = create_order(
                customer_name=data.get('customer_name', ''),
                customer_phone=data.get('customer_phone', ''),
                customer_email=data.get('customer_email', ''),
                address=data.get('address', ''),
                comment=data.get('comment', ''),
                items=items,
                source_ip=None)
        except InsufficientStockError as error:
            return Response({'error': str(error)},
                            status=status.HTTP_409_CONFLICT)
        except (ValueError, TypeError, KeyError) as error:
            return Response({'error': str(error)},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response(self.get_serializer(order).data,
                        status=status.HTTP_201_CREATED)

    queryset = (Order.objects
                .prefetch_related('items__product')
                .select_related('outbound_document'))
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['status']
    ordering_fields = ['created_at', 'number']

    def get_permissions(self):
        """Права зависят от действия, а не от раздела в целом.

        Заказ проходит через руки двух разных людей. Менеджер связывается
        с покупателем и подтверждает или отменяет заказ — это работа с
        клиентом. Отгрузка же списывает товар со склада, то есть меняет
        остатки, и относится к складским операциям наравне с расходным
        документом. Поэтому у неё требования строже.
        """
        if self.action == 'ship':
            return [CanEditDocuments()]
        if self.action in ('confirm', 'cancel', 'create', 'update',
                           'partial_update', 'destroy'):
            return [CanProcessOrders()]
        return [IsAuthenticated()]

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
