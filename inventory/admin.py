from django.contrib import admin

from .models import Inventory, InventoryItem


class InventoryItemInline(admin.TabularInline):
    model = InventoryItem
    extra = 0
    readonly_fields = ('system_quantity',)


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ('number', 'warehouse', 'status', 'created_at',
                    'completed_at')
    list_filter = ('status', 'warehouse')
    inlines = [InventoryItemInline]
    readonly_fields = ('status', 'created_at', 'completed_at')
