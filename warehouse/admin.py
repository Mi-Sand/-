"""Регистрация моделей в административной панели Django."""
from django.contrib import admin

from .models import (InboundDocument, InboundItem, Material, Order, OrderItem,
                     OutboundDocument, OutboundItem, PriceHistory, Product,
                     Stock, StockMovement, Supplier, Warehouse)


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


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = ('recorded_at', 'material', 'product', 'price',
                    'supplier')
    list_filter = ('recorded_at', 'supplier')


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product', 'quantity', 'price')
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Заказы покупателей.

    Только просмотр: заказы создаются с витрины, а статус меняется через
    подтверждение, отгрузку и отмену — там проверяются допустимые переходы
    и остатки на складе. Правка статуса руками обошла бы эти проверки.
    Редактируются лишь реквизиты покупателя-организации, нужные для
    накладной и УПД.
    """

    list_display = ('number', 'created_at', 'customer_name',
                    'customer_phone', 'status', 'counterparty')
    list_filter = ('status', 'created_at')
    search_fields = ('number', 'customer_name', 'customer_phone',
                     'customer_email')
    date_hierarchy = 'created_at'
    inlines = [OrderItemInline]
    autocomplete_fields = ('counterparty',)
    readonly_fields = ('number', 'customer_name', 'customer_phone',
                       'customer_email', 'address', 'comment', 'status',
                       'created_at', 'outbound_document')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
