"""Регистрация моделей в административной панели Django."""
from django.contrib import admin

from .models import (InboundDocument, InboundItem, Material, Order,
                     OrderItem, OutboundDocument, OutboundItem, PriceHistory,
                     Product, ProductionMaterial, ProductionRun,
                     Stock, StockMovement, Supplier, Unit, Warehouse)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ('name', 'full_name', 'code', 'okei', 'builtin')
    list_filter = ('builtin',)
    search_fields = ('name', 'full_name', 'code')


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'unit', 'reorder_point')
    list_filter = ('category', 'unit')
    search_fields = ('name', 'description')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('article_number', 'name', 'category', 'size', 'color',
                    'selling_price', 'status')
    list_filter = ('category', 'status', 'size')
    search_fields = ('article_number', 'name', 'color')


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('name', 'type', 'location', 'capacity')
    list_filter = ('type',)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'inn', 'contact_person', 'phone')
    search_fields = ('name', 'inn')


class InboundItemInline(admin.TabularInline):
    model = InboundItem
    extra = 1


@admin.register(InboundDocument)
class InboundDocumentAdmin(admin.ModelAdmin):
    list_display = ('doc_number', 'doc_date', 'supplier', 'warehouse',
                    'processed')
    list_filter = ('processed', 'warehouse', 'supplier')
    search_fields = ('doc_number',)
    inlines = [InboundItemInline]
    readonly_fields = ('processed', 'created_at')


class OutboundItemInline(admin.TabularInline):
    model = OutboundItem
    extra = 1


@admin.register(OutboundDocument)
class OutboundDocumentAdmin(admin.ModelAdmin):
    list_display = ('doc_number', 'doc_date', 'warehouse', 'purpose',
                    'processed')
    list_filter = ('processed', 'warehouse', 'purpose')
    search_fields = ('doc_number', 'production_order')
    inlines = [OutboundItemInline]
    readonly_fields = ('processed', 'created_at')


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ('item_name', 'warehouse', 'quantity', 'last_updated')
    list_filter = ('warehouse',)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'movement_type', 'item_name', 'warehouse',
                    'quantity', 'user')
    list_filter = ('movement_type', 'warehouse', 'created_at')
    readonly_fields = ('created_at',)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Заказы покупателей.

    Нужны в панели и сами по себе, и для подбора заказа при вводе
    оплаты: без поиска по номеру пришлось бы выбирать из выпадающего
    списка, где через год окажутся тысячи строк.
    """

    list_display = ('number', 'created_at', 'customer_name', 'status')
    list_filter = ('status', 'created_at')
    search_fields = ('number', 'customer_name', 'customer_phone',
                     'customer_email')
    date_hierarchy = 'created_at'
    inlines = [OrderItemInline]


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = ('recorded_at', 'material', 'product', 'price',
                    'supplier')
    list_filter = ('recorded_at', 'supplier')


class ProductionMaterialInline(admin.TabularInline):
    model = ProductionMaterial
    extra = 0
    readonly_fields = ('material', 'quantity', 'unit_price', 'price_known')
    can_delete = False


@admin.register(ProductionRun)
class ProductionRunAdmin(admin.ModelAdmin):
    """Выпуски продукции — только просмотр.

    Записи создаёт операция производства вместе со списанием материалов и
    оприходованием изделий. Править их руками значило бы разойтись с
    журналом движения.
    """

    list_display = ('number', 'created_at', 'product', 'quantity',
                    'unit_cost', 'planned_unit_cost', 'pricing_complete')
    list_filter = ('pricing_complete', 'created_at', 'product_warehouse')
    search_fields = ('number', 'product__name', 'product__article_number')
    date_hierarchy = 'created_at'
    inlines = [ProductionMaterialInline]
    readonly_fields = ('number', 'product', 'quantity', 'product_warehouse',
                       'material_warehouse', 'material_cost', 'unit_cost',
                       'planned_unit_cost', 'pricing_complete',
                       'created_at', 'created_by')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
