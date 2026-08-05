"""Административная панель для данных бухгалтерии."""
from django.contrib import admin

from .models import CompanyRequisites, Counterparty, Payment


@admin.register(CompanyRequisites)
class CompanyRequisitesAdmin(admin.ModelAdmin):
    """Реквизиты своей организации.

    Запись должна быть одна, поэтому кнопка «Добавить» пропадает, как
    только реквизиты заполнены, а удалять их нельзя вовсе: без них не
    печатается ни один документ.
    """

    list_display = ('short_name', 'inn', 'kpp', 'vat_rate', 'updated_at')

    fieldsets = (
        ('Организация', {
            'fields': ('short_name', 'full_name', 'inn', 'kpp', 'ogrn'),
        }),
        ('Адреса и связь', {
            'fields': ('legal_address', 'postal_address', 'phone'),
        }),
        ('Банковские реквизиты', {
            'fields': ('bank_name', 'bank_bik', 'settlement_account',
                       'correspondent_account'),
            'description': 'Печатаются в счёте — по ним покупатель платит. '
                           'Сверьте с договором банковского обслуживания.',
        }),
        ('Подписи в документах', {
            'fields': ('director_position', 'director_name',
                       'accountant_name'),
        }),
        ('Налогообложение', {
            'fields': ('vat_rate', 'vat_included_in_price'),
            'description': 'От ставки зависит, как считается и печатается '
                           'налог во всех бланках.',
        }),
    )

    def has_add_permission(self, request):
        return not CompanyRequisites.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Counterparty)
class CounterpartyAdmin(admin.ModelAdmin):
    list_display = ('short_name', 'inn', 'kpp', 'phone', 'created_at')
    search_fields = ('short_name', 'inn')
    list_filter = ('created_at',)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('order', 'amount', 'paid_at', 'method',
                    'document_number')
    list_filter = ('method', 'paid_at')
    search_fields = ('order__number', 'document_number',
                     'order__customer_name')
    date_hierarchy = 'paid_at'
    autocomplete_fields = ('order',)
