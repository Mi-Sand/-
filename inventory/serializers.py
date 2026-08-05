"""Сериализаторы модуля инвентаризации."""
from rest_framework import serializers

from .models import Inventory, InventoryItem


class InventoryItemSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(read_only=True)
    difference = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = InventoryItem
        fields = ['id', 'material', 'product', 'item_name',
                  'system_quantity', 'actual_quantity', 'difference']
        read_only_fields = ['system_quantity']


class InventorySerializer(serializers.ModelSerializer):
    items = InventoryItemSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(
        source='warehouse.name', read_only=True)
    status_display = serializers.CharField(
        source='get_status_display', read_only=True)
    discrepancy_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Inventory
        fields = ['id', 'number', 'warehouse', 'warehouse_name', 'status',
                  'status_display', 'created_by', 'created_at',
                  'completed_at', 'discrepancy_count', 'items']
        read_only_fields = ['status', 'created_by', 'created_at',
                            'completed_at']

    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            validated_data['created_by'] = request.user
        return super().create(validated_data)
