"""Обработчики API модуля инвентаризации."""
from decimal import Decimal

from rest_framework import status, viewsets
from rest_framework.decorators import action
from accounts.permissions import CanEditDocuments
from rest_framework.response import Response

from .models import Inventory, InventoryItem
from .serializers import InventorySerializer
from .services import build_inventory_sheet, finalize_inventory


class InventoryViewSet(viewsets.ModelViewSet):
    queryset = (Inventory.objects
                .select_related('warehouse', 'created_by')
                .prefetch_related('items__material', 'items__product'))
    serializer_class = InventorySerializer
    permission_classes = [CanEditDocuments]
    filterset_fields = ['warehouse', 'status']

    @action(detail=True, methods=['post'])
    def build_sheet(self, request, pk=None):
        """Заполнить опись текущими остатками склада."""
        inv = build_inventory_sheet(pk)
        return Response(self.get_serializer(inv).data)

    @action(detail=True, methods=['post'])
    def save_counts(self, request, pk=None):
        """Сохранить фактические остатки, введённые при пересчёте.

        Ожидает список: [{"item_id": 1, "actual_quantity": 42}, ...]

        Присланное проверяется целиком, до первой записи в базу. Раньше
        не проверялось ничего: пересчёт «абв» доходил до базы и
        оборачивался ошибкой сервера, а пересчёт «−50» записывался как
        есть и уводил остаток в минус при завершении описи.
        """
        counts = request.data.get('counts', [])
        if not isinstance(counts, (list, tuple)):
            return Response({'error': 'Неверный формат пересчёта'},
                            status=status.HTTP_400_BAD_REQUEST)

        rows = []
        for row in counts:
            if not isinstance(row, dict):
                return Response({'error': 'Неверный формат строки пересчёта'},
                                status=status.HTTP_400_BAD_REQUEST)
            try:
                item_id = int(row.get('item_id'))
            except (TypeError, ValueError):
                return Response({'error': 'В строке не указана позиция описи'},
                                status=status.HTTP_400_BAD_REQUEST)

            quantity = row.get('actual_quantity')
            # Пустое поле — позицию просто не считали, это не ошибка
            if quantity is None or quantity == '':
                rows.append((item_id, None))
                continue
            try:
                quantity = Decimal(str(quantity))
            except (TypeError, ArithmeticError):
                return Response(
                    {'error': 'Фактический остаток — не число'},
                    status=status.HTTP_400_BAD_REQUEST)
            # «Бесконечность» и «не число» переводятся успешно, но любое
            # сравнение с ними ложно — проверку ниже они бы прошли молча
            if not quantity.is_finite():
                return Response(
                    {'error': 'Фактический остаток — не число'},
                    status=status.HTTP_400_BAD_REQUEST)
            if quantity < 0:
                return Response(
                    {'error': 'Фактический остаток не может быть '
                              'отрицательным: на полке лежит либо '
                              'что-то, либо ничего'},
                    status=status.HTTP_400_BAD_REQUEST)
            rows.append((item_id, quantity))

        for item_id, quantity in rows:
            InventoryItem.objects.filter(
                pk=item_id, inventory_id=pk
            ).update(actual_quantity=quantity)
        inv = self.get_object()
        return Response(self.get_serializer(inv).data)

    @action(detail=True, methods=['post'])
    def finalize(self, request, pk=None):
        """Завершить инвентаризацию и провести расхождения."""
        try:
            discrepancies = finalize_inventory(pk, user=request.user)
            return Response({
                'status': 'ok',
                'discrepancies': [{
                    'item': d['item'],
                    'system': str(d['system']),
                    'actual': str(d['actual']),
                    'difference': str(d['difference']),
                } for d in discrepancies],
            })
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
