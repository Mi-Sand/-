from django.contrib import admin

from .models import AuditEntry


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    """Только просмотр: журнал, который можно править, ничего не доказывает."""

    list_display = ('happened_at', 'user_label', 'action', 'model_title',
                    'object_label')
    list_filter = ('action', 'model_label', 'happened_at')
    search_fields = ('user_label', 'object_label')
    date_hierarchy = 'happened_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
