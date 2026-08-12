"""
Представления страниц веб-интерфейса.

Отдают HTML-шаблоны клиентской части. Вся работа с данными на страницах
идёт через REST API (fetch + JSON), как описано в п. 2.1.1 отчёта.
Все страницы защищены входом в систему.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render


@login_required
def dashboard_page(request):
    return render(request, 'dashboard.html', {'active': 'dashboard'})


@login_required
def materials_page(request):
    return render(request, 'materials.html', {'active': 'materials'})


@login_required
def products_page(request):
    return render(request, 'products.html', {'active': 'products'})


@login_required
def inbound_page(request):
    return render(request, 'inbound.html', {'active': 'inbound'})


@login_required
def outbound_page(request):
    return render(request, 'outbound.html', {'active': 'outbound'})


@login_required
def inventory_page(request):
    return render(request, 'inventory.html', {'active': 'inventory'})


@login_required
def trash_page(request):
    """Корзина удалённых документов."""
    from django.conf import settings
    return render(request, 'trash.html', {
        'active': 'trash',
        'keep_days': getattr(settings, 'TRASH_KEEP_DAYS', 30),
    })


@login_required
def reports_page(request):
    return render(request, 'reports.html', {'active': 'reports'})


@login_required
def catalog_page(request):
    return render(request, 'catalog.html', {'active': 'catalog'})


@login_required
def production_page(request):
    return render(request, 'production.html', {'active': 'production'})


@login_required
def orders_page(request):
    return render(request, 'orders.html', {'active': 'orders'})


@login_required
def employees_page(request):
    # Страница сотрудников доступна только администратору или суперпользователю
    if not (request.user.is_superuser or
            getattr(request.user, 'role', None) == 'admin'):
        return redirect('/')
    return render(request, 'employees.html', {'active': 'employees'})
