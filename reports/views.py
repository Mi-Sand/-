"""
Отчёты системы складского учёта.

Формируются пять видов отчётов (см. п. 2.2.5 отчёта):
1. Остатки на дату — текущие остатки по складам и категориям
2. Движение товаров за период — приходы, расходы, корректировки
3. Расход материалов по производственным заказам
4. Результаты инвентаризаций — расхождения и недостачи
5. Перечень позиций для закупки — с остатком ниже минимума

Отчёты выводятся в JSON и экспортируются в Excel.
"""
from datetime import datetime, timedelta
from io import BytesIO

from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce
from django.contrib.auth.decorators import login_required
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from warehouse.models import Material, Stock, StockMovement, Product
from inventory.models import Inventory


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def stock_report(request):
    """Остатки на дату: текущие остатки всех товаров по складам."""
    date = request.query_params.get('date', datetime.now().date())
    warehouse_id = request.query_params.get('warehouse')

    stocks = Stock.objects.select_related('warehouse', 'material', 'product')

    if warehouse_id:
        stocks = stocks.filter(warehouse_id=warehouse_id)

    stocks = stocks.filter(quantity__gt=0)

    rows = []
    for stock in stocks:
        material = stock.material
        product = stock.product

        rows.append({
            'склад': stock.warehouse.name,
            'тип': 'Материал' if material else 'Продукция',
            'наименование': material.name if material else product.name,
            'артикул': product.article_number if product else '—',
            'размер': product.size if product else '—',
            'цвет': product.color if product else '—',
            'количество': float(stock.quantity),
            'единица': material.get_unit_display() if material else 'шт.',
        })

    return Response({
        'отчёт': 'Остатки на дату',
        'дата': date,
        'всего_позиций': len(rows),
        'строки': rows,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def movement_report(request):
    """Движение товаров за период."""
    days_back = int(request.query_params.get('days', 30))
    from_date = datetime.now().date() - timedelta(days=days_back)
    to_date = datetime.now().date()
    warehouse_id = request.query_params.get('warehouse')

    movements = (StockMovement.objects
                .select_related('warehouse', 'material', 'product', 'user')
                .filter(created_at__date__gte=from_date,
                        created_at__date__lte=to_date))

    if warehouse_id:
        movements = movements.filter(warehouse_id=warehouse_id)

    inbound_qty = (movements.filter(movement_type='in')
                  .aggregate(Sum('quantity'))['quantity__sum'] or 0)
    outbound_qty = (movements.filter(movement_type='out')
                   .aggregate(Sum('quantity'))['quantity__sum'] or 0)

    rows = []
    for movement in movements.order_by('-created_at'):
        rows.append({
            'дата': str(movement.created_at.date()),
            'время': movement.created_at.time().isoformat(),
            'тип': movement.get_movement_type_display(),
            'товар': movement.item_name,
            'количество': float(movement.quantity),
            'склад': movement.warehouse.name,
            'пользователь': movement.user.get_full_name() if movement.user else '—',
            'документ': movement.document_id or '—',
        })

    return Response({
        'отчёт': 'Движение товаров',
        'с_даты': str(from_date),
        'по_дату': str(to_date),
        'приход_кол': float(inbound_qty),
        'расход_кол': float(outbound_qty),
        'всего_операций': len(rows),
        'строки': rows,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reorder_report(request):
    """Перечень позиций для закупки — с остатком ниже минимума."""
    materials = (Material.objects
                 .annotate(
                     stock_qty=Coalesce(
                         Sum('stock__quantity'),
                         Value(0, output_field=DecimalField())))
                 .filter(stock_qty__lt=F('reorder_point')))

    rows = []
    for material in materials:
        shortage = material.reorder_point - material.stock_qty
        rows.append({
            'наименование': material.name,
            'категория': material.get_category_display(),
            'текущий_остаток': float(material.stock_qty),
            'минимум': float(material.reorder_point),
            'недостаток': float(shortage),
            'единица': material.get_unit_display(),
            'описание': material.description,
        })

    return Response({
        'отчёт': 'Позиции для закупки',
        'дата': str(datetime.now().date()),
        'позиций_в_дефиците': len(rows),
        'строки': rows,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def inventory_report(request):
    """Результаты инвентаризаций."""
    inventory_id = request.query_params.get('inventory')

    if inventory_id:
        inventories = Inventory.objects.filter(pk=inventory_id)
    else:
        inventories = (Inventory.objects
                      .filter(status='completed')
                      .order_by('-completed_at')[:10])

    results = []
    for inv in inventories:
        shortages = []
        surpluses = []
        for item in inv.items.all():
            if item.difference < 0:
                shortages.append({
                    'товар': item.item_name,
                    'величина': float(abs(item.difference)),
                })
            elif item.difference > 0:
                surpluses.append({
                    'товар': item.item_name,
                    'величина': float(item.difference),
                })

        results.append({
            'номер': inv.number,
            'склад': inv.warehouse.name,
            'дата_завершения': str(inv.completed_at.date()) if inv.completed_at else None,
            'недостач': len(shortages),
            'излишков': len(surpluses),
            'недостачи': shortages[:5],
            'излишки': surpluses[:5],
        })

    return Response({
        'отчёт': 'Результаты инвентаризаций',
        'дата': str(datetime.now().date()),
        'всего_инвентаризаций': len(results),
        'результаты': results,
    })


@login_required
def stock_report_export(request):
    """Экспорт отчёта об остатках в Excel."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        from django.http import HttpResponse
        return HttpResponse('Библиотека openpyxl не установлена', status=400)

    # Получить данные отчёта
    stocks = Stock.objects.select_related(
        'warehouse', 'material', 'product').filter(quantity__gt=0)

    # Создать книгу
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = 'Остатки'

    # Заголовок
    headers = ['Склад', 'Тип', 'Наименование', 'Артикул', 'Размер',
              'Цвет', 'Количество', 'Единица']
    worksheet.append(headers)

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='366092', end_color='366092',
                            fill_type='solid')
    for cell in worksheet[1]:
        cell.font = header_font
        cell.fill = header_fill

    # Данные
    for stock in stocks:
        material = stock.material
        product = stock.product
        worksheet.append([
            stock.warehouse.name,
            'Материал' if material else 'Продукция',
            material.name if material else product.name,
            product.article_number if product else '—',
            product.size if product else '—',
            product.color if product else '—',
            float(stock.quantity),
            material.get_unit_display() if material else 'шт.',
        ])

    # Установить ширину столбцов
    worksheet.column_dimensions['A'].width = 20
    worksheet.column_dimensions['B'].width = 12
    worksheet.column_dimensions['C'].width = 25
    worksheet.column_dimensions['D'].width = 12
    worksheet.column_dimensions['E'].width = 10
    worksheet.column_dimensions['F'].width = 12
    worksheet.column_dimensions['G'].width = 12
    worksheet.column_dimensions['H'].width = 12

    # Сохранить в памяти и отправить как файл (HttpResponse, не DRF Response —
    # иначе DRF пытается декодировать бинарные байты как UTF-8 и падает)
    from django.http import HttpResponse
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument'
                     '.spreadsheetml.sheet')
    # Имя файла латиницей в filename + кириллица в filename* (RFC 5987)
    response['Content-Disposition'] = (
        "attachment; filename=\"stock.xlsx\"; "
        "filename*=UTF-8''%D0%BE%D1%81%D1%82%D0%B0%D1%82%D0%BA%D0%B8.xlsx")
    return response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def expiry_report(request):
    """Партии с истекающим сроком годности.

    Параметр ?days= задаёт горизонт предупреждения в днях (по умолчанию 30).
    Просроченное показывается всегда, независимо от горизонта.
    """
    from warehouse.expiry import DEFAULT_WARNING_DAYS, expiring_batches

    raw_days = request.query_params.get('days')
    if raw_days in (None, ''):
        days = DEFAULT_WARNING_DAYS
    else:
        try:
            days = int(raw_days)
        except (TypeError, ValueError):
            return Response(
                {'error': f'Неверное значение параметра days: {raw_days!r}'},
                status=400)
        days = max(0, min(days, 365))

    rows = expiring_batches(days=days)

    return Response({
        'отчёт': 'Партии с истекающим сроком годности',
        'дата': str(datetime.now().date()),
        'горизонт_дней': days,
        'просрочено': sum(1 for r in rows if r['expired']),
        'истекает': sum(1 for r in rows if not r['expired']),
        'строки': [{
            'товар': r['item_name'],
            'партия': r['batch_number'],
            'годен_до': str(r['expiry_date']),
            'дней_осталось': r['days_left'],
            'просрочено': r['expired'],
            'поступило': float(r['quantity_received']),
            'остаток_позиции': float(r['stock_remaining']),
            'склад': r['warehouse'],
            'поставщик': r['supplier'],
            'документ': r['document'],
        } for r in rows],
        'примечание': (
            'Остатки ведутся по позиции склада, а не по партиям, поэтому '
            'графа «остаток позиции» показывает общий остаток товара, а не '
            'то, сколько осталось именно от этой партии.'),
    })
