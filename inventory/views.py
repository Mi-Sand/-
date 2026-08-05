"""Обработчики API модуля инвентаризации."""
from decimal import Decimal

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Inventory, InventoryItem
from .serializers import InventorySerializer
from .services import build_inventory_sheet, finalize_inventory


class InventoryViewSet(viewsets.ModelViewSet):
    queryset = (Inventory.objects
                .select_related('warehouse', 'created_by')
                .prefetch_related('items__material', 'items__product'))
    serializer_class = InventorySerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['warehouse', 'status']

    @action(detail=True, methods=['post'])
    def build_sheet(self, request, pk=None):
        """Заполнить опись текущими остатками склада."""
        inv = self.get_object()
        try:
            inv = build_inventory_sheet(inv.pk)
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(inv).data)

    @action(detail=True, methods=['post'])
    def save_counts(self, request, pk=None):
        """Сохранить фактические остатки, введённые при пересчёте.

        Ожидает список: [{"item_id": 1, "actual_quantity": 42}, ...]

        Значения приходят из формы пересчёта, поэтому проверяются: буквы
        вместо числа или отрицательный остаток попадали бы прямо в базу и
        всплыли бы уже при завершении инвентаризации.
        """
        inv = self.get_object()
        if inv.status == 'completed':
            return Response(
                {'error': 'Инвентаризация завершена — пересчёт изменить '
                          'нельзя.'},
                status=status.HTTP_400_BAD_REQUEST)

        counts = request.data.get('counts', [])
        if not isinstance(counts, (list, tuple)):
            return Response({'error': 'Неверный формат данных пересчёта'},
                            status=status.HTTP_400_BAD_REQUEST)

        prepared = []
        for row in counts:
            if not isinstance(row, dict):
                return Response({'error': 'Неверный формат строки пересчёта'},
                                status=status.HTTP_400_BAD_REQUEST)
            try:
                item_id = int(row.get('item_id'))
            except (TypeError, ValueError):
                return Response(
                    {'error': f'Неверная строка описи: '
                              f'{row.get("item_id")!r}'},
                    status=status.HTTP_400_BAD_REQUEST)

            raw = row.get('actual_quantity')
            if raw in (None, ''):
                prepared.append((item_id, None))
                continue
            try:
                quantity = Decimal(str(raw))
            except (ArithmeticError, TypeError, ValueError):
                return Response({'error': f'Неверное количество: {raw!r}'},
                                status=status.HTTP_400_BAD_REQUEST)
            if not quantity.is_finite() or quantity < 0:
                return Response(
                    {'error': f'Фактический остаток не может быть '
                              f'отрицательным: {raw!r}'},
                    status=status.HTTP_400_BAD_REQUEST)
            prepared.append((item_id, quantity))

        with transaction.atomic():
            for item_id, quantity in prepared:
                InventoryItem.objects.filter(
                    pk=item_id, inventory_id=inv.pk
                ).update(actual_quantity=quantity)

        inv.refresh_from_db()
        return Response(self.get_serializer(inv).data)

    @action(detail=True, methods=['post'])
    def finalize(self, request, pk=None):
        """Завершить инвентаризацию и провести расхождения."""
        inv = self.get_object()
        try:
            discrepancies = finalize_inventory(inv.pk, user=request.user)
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
