"""Обработчики API модуля инвентаризации."""
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
        inv = build_inventory_sheet(pk)
        return Response(self.get_serializer(inv).data)

    @action(detail=True, methods=['post'])
    def save_counts(self, request, pk=None):
        """Сохранить фактические остатки, введённые при пересчёте.

        Ожидает список: [{"item_id": 1, "actual_quantity": 42}, ...]
        """
        counts = request.data.get('counts', [])
        for row in counts:
            InventoryItem.objects.filter(
                pk=row.get('item_id'), inventory_id=pk
            ).update(actual_quantity=row.get('actual_quantity'))
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
