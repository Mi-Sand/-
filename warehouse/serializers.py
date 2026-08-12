"""
Сериализаторы REST API (фрагмент 11 отчёта).

Помимо преобразования моделей в JSON и обратно, сериализаторы проверяют
входные данные: строка документа не может ссылаться одновременно на материал
и на продукцию, количество должно быть положительным и т. п.
"""
from django.db import transaction
from rest_framework import serializers

from .models import (InboundDocument, InboundItem, Material,
                     OutboundDocument, OutboundItem, Product, ProductPhoto,
                     Stock, StockMovement, Supplier, Unit, Warehouse)


# --- Справочники -----------------------------------------------------------
class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ['id', 'code', 'name', 'full_name', 'okei', 'builtin',
                  'sort_order']
        # Встроенные единицы отличать нужно, а назначать — нет: иначе
        # свою единицу можно объявить встроенной и обойти запрет на
        # удаление.
        read_only_fields = ['builtin']

    def validate_code(self, value):
        code = (value or '').strip()
        if not code:
            raise serializers.ValidationError('Код не может быть пустым.')
        return code


class MaterialSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(
        source='get_category_display', read_only=True)
    unit_display = serializers.CharField(
        source='get_unit_display', read_only=True)

    class Meta:
        model = Material
        fields = ['id', 'name', 'article_number', 'barcode', 'color',
                  'description', 'unit', 'unit_display',
                  'category', 'category_display', 'reorder_point']

    def validate_unit(self, value):
        """Единица должна быть из справочника.

        Список выбора из модели убран — единицы теперь заводят сами, —
        и без этой проверки в поле прошла бы любая строка. Материал с
        единицей «шт» вместо «шт.» выглядит как настоящий, а в отчётах
        считается отдельной строкой.
        """
        if not Unit.objects.filter(code=value).exists():
            known = ', '.join(Unit.objects.values_list('code', flat=True))
            raise serializers.ValidationError(
                f'Нет такой единицы измерения: «{value}». '
                f'Есть: {known}. Новую заводят в справочниках.')
        return value


class ProductPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductPhoto
        fields = ['id', 'image', 'sort_order']


class ProductSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(
        source='get_category_display', read_only=True)
    # Дополнительные фотографии — списком, обложка остаётся в поле photo.
    extra_photos = ProductPhotoSerializer(
        source='photos', many=True, read_only=True)
    # Необязательные поля: задать начальный остаток при создании продукции
    initial_stock = serializers.DecimalField(
        max_digits=10, decimal_places=2, write_only=True,
        required=False, allow_null=True)
    initial_warehouse = serializers.PrimaryKeyRelatedField(
        queryset=Warehouse.objects.all(), write_only=True,
        required=False, allow_null=True)

    class Meta:
        model = Product
        fields = ['id', 'article_number', 'barcode', 'name', 'category',
                  'category_display', 'size', 'color', 'cost',
                  'selling_price', 'status', 'description', 'photo', 'video',
                  'extra_photos', 'initial_stock', 'initial_warehouse']

    def create(self, validated_data):
        initial_stock = validated_data.pop('initial_stock', None)
        initial_warehouse = validated_data.pop('initial_warehouse', None)
        product = super().create(validated_data)
        if initial_stock and initial_warehouse and initial_stock > 0:
            stock, _ = Stock.objects.get_or_create(
                warehouse=initial_warehouse, product=product,
                defaults={'quantity': 0})
            stock.quantity = stock.quantity + initial_stock
            stock.save()
            user = None
            request = self.context.get('request')
            if request and request.user.is_authenticated:
                user = request.user
            StockMovement.objects.create(
                warehouse=initial_warehouse, product=product,
                movement_type='in', quantity=initial_stock, user=user)
        return product


class WarehouseSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(
        source='get_type_display', read_only=True)

    class Meta:
        model = Warehouse
        fields = ['id', 'name', 'type', 'type_display', 'location',
                  'capacity']


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ['id', 'name', 'inn', 'contact_person', 'phone', 'email']


# --- Строки и документы прихода --------------------------------------------
class InboundItemSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(read_only=True)

    class Meta:
        model = InboundItem
        fields = ['id', 'material', 'product', 'item_name', 'quantity',
                  'unit_price']

    def validate(self, data):
        # Ровно одно из полей: материал ИЛИ продукция
        if bool(data.get('material')) == bool(data.get('product')):
            raise serializers.ValidationError(
                'Укажите материал или продукцию, но не оба')
        if data['quantity'] <= 0:
            raise serializers.ValidationError(
                'Количество должно быть положительным')
        return data


class InboundDocumentSerializer(serializers.ModelSerializer):
    items = InboundItemSerializer(many=True)
    total_sum = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True)
    supplier_name = serializers.CharField(
        source='supplier.name', read_only=True)
    warehouse_name = serializers.CharField(
        source='warehouse.name', read_only=True)

    class Meta:
        model = InboundDocument
        fields = ['id', 'doc_number', 'doc_date', 'supplier',
                  'supplier_name', 'warehouse', 'warehouse_name',
                  'created_by', 'processed', 'total_sum', 'items',
                  'created_at']
        read_only_fields = ['processed', 'created_by', 'created_at']

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            validated_data['created_by'] = request.user
        doc = InboundDocument.objects.create(**validated_data)
        for item in items_data:
            InboundItem.objects.create(inbound_doc=doc, **item)
        return doc

    @transaction.atomic
    def update(self, instance, validated_data):
        if instance.processed:
            raise serializers.ValidationError(
                'Проведённый документ нельзя изменять')
        items_data = validated_data.pop('items', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if items_data is not None:
            instance.items.all().delete()
            for item in items_data:
                InboundItem.objects.create(inbound_doc=instance, **item)
        return instance


# --- Строки и документы расхода --------------------------------------------
class OutboundItemSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(read_only=True)

    class Meta:
        model = OutboundItem
        fields = ['id', 'material', 'product', 'item_name', 'quantity',
                  'unit_price']

    def validate(self, data):
        if bool(data.get('material')) == bool(data.get('product')):
            raise serializers.ValidationError(
                'Укажите материал или продукцию, но не оба')
        if data['quantity'] <= 0:
            raise serializers.ValidationError(
                'Количество должно быть положительным')
        return data


class OutboundDocumentSerializer(serializers.ModelSerializer):
    items = OutboundItemSerializer(many=True)
    total_sum = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True)
    warehouse_name = serializers.CharField(
        source='warehouse.name', read_only=True)
    purpose_display = serializers.CharField(
        source='get_purpose_display', read_only=True)

    class Meta:
        model = OutboundDocument
        fields = ['id', 'doc_number', 'doc_date', 'warehouse',
                  'warehouse_name', 'purpose', 'purpose_display',
                  'production_order', 'created_by', 'processed',
                  'total_sum', 'items', 'created_at']
        read_only_fields = ['processed', 'created_by', 'created_at']

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            validated_data['created_by'] = request.user
        doc = OutboundDocument.objects.create(**validated_data)
        for item in items_data:
            OutboundItem.objects.create(outbound_doc=doc, **item)
        return doc

    @transaction.atomic
    def update(self, instance, validated_data):
        if instance.processed:
            raise serializers.ValidationError(
                'Проведённый документ нельзя изменять')
        items_data = validated_data.pop('items', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if items_data is not None:
            instance.items.all().delete()
            for item in items_data:
                OutboundItem.objects.create(outbound_doc=instance, **item)
        return instance


# --- Служебные -------------------------------------------------------------
class StockSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(read_only=True)
    warehouse_name = serializers.CharField(
        source='warehouse.name', read_only=True)
    unit = serializers.SerializerMethodField()

    class Meta:
        model = Stock
        fields = ['id', 'warehouse', 'warehouse_name', 'material',
                  'product', 'item_name', 'unit', 'quantity',
                  'last_updated']

    def get_unit(self, obj):
        if obj.material:
            return obj.material.get_unit_display()
        return 'шт.'


class StockMovementSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(read_only=True)
    movement_type_display = serializers.CharField(
        source='get_movement_type_display', read_only=True)
    warehouse_name = serializers.CharField(
        source='warehouse.name', read_only=True)
    user_name = serializers.CharField(
        source='user.get_full_name', read_only=True, default='')
    # Дата в привычном виде «02.08.2026 19:07» — сырой формат базы
    # в интерфейсе читать неудобно
    created_display = serializers.DateTimeField(
        source='created_at', format='%d.%m.%Y %H:%M', read_only=True)

    class Meta:
        model = StockMovement
        fields = ['id', 'warehouse', 'warehouse_name', 'item_name',
                  'movement_type', 'movement_type_display', 'quantity',
                  'created_display',
                  'document_id', 'user', 'user_name', 'created_at']
