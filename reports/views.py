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
from datetime import date as date_cls, timedelta
from io import BytesIO

from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from warehouse.models import Material, Stock, StockMovement, Product
from inventory.models import Inventory


def _int_param(value, default, minimum, maximum):
    """Прочитать целочисленный параметр запроса в заданных границах.

    Параметры приходят из адресной строки, где может оказаться что угодно.
    Непонятное значение — повод ответить «неверный параметр», а не упасть.
    """
    if value in (None, ''):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'Неверное значение параметра: {value!r}')
    return max(minimum, min(number, maximum))


def _date_param(value):
    """Прочитать дату отчёта в формате ГГГГ-ММ-ДД."""
    if value in (None, ''):
        return timezone.localdate()
    try:
        return date_cls.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f'Неверная дата: {value!r}. Ожидается ГГГГ-ММ-ДД')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def stock_report(request):
    """Остатки на дату.

    На сегодняшний день отдаёт текущие остатки. Для прошедшей даты остаток
    восстанавливается по журналу движения: от текущего значения отматываются
    назад все операции, случившиеся после запрошенного дня. Приход,
    сделанный позже, вычитается; расход — возвращается; корректировка
    инвентаризации учитывается со своим знаком.
    """
    try:
        report_date = _date_param(request.query_params.get('date'))
    except ValueError as e:
        return Response({'error': str(e)},
                        status=http_status.HTTP_400_BAD_REQUEST)
    warehouse_id = request.query_params.get('warehouse')

    stocks = Stock.objects.select_related('warehouse', 'material', 'product')
    if warehouse_id:
        stocks = stocks.filter(warehouse_id=warehouse_id)

    today = timezone.localdate()
    historical = report_date < today

    # Сумма движений после запрошенной даты — по каждой позиции склада
    rollback = {}
    if historical:
        later = StockMovement.objects.filter(created_at__date__gt=report_date)
        if warehouse_id:
            later = later.filter(warehouse_id=warehouse_id)
        for row in (later
                    .values('warehouse_id', 'material_id', 'product_id',
                            'movement_type')
                    .annotate(total=Coalesce(
                        Sum('quantity'),
                        Value(0, output_field=DecimalField())))):
            key = (row['warehouse_id'], row['material_id'], row['product_id'])
            delta = row['total']
            if row['movement_type'] == 'out':
                delta = -delta
            # 'in' и 'adjust' уже приходят со знаком «плюс к остатку»
            rollback[key] = rollback.get(key, 0) + delta

    rows = []
    for stock in stocks:
        quantity = stock.quantity
        if historical:
            key = (stock.warehouse_id, stock.material_id, stock.product_id)
            quantity = quantity - rollback.get(key, 0)
        if quantity <= 0:
            continue

        material = stock.material
        product = stock.product
        rows.append({
            'склад': stock.warehouse.name,
            'тип': 'Материал' if material else 'Продукция',
            'наименование': material.name if material else product.name,
            'артикул': product.article_number if product else '—',
            'размер': product.size if product else '—',
            'цвет': product.color if product else '—',
            'количество': float(quantity),
            'единица': material.get_unit_display() if material else 'шт.',
        })

    return Response({
        'отчёт': 'Остатки на дату',
        'дата': str(report_date),
        'на_сегодня': not historical,
        'всего_позиций': len(rows),
        'строки': rows,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def movement_report(request):
    """Движение товаров за период."""
    try:
        days_back = _int_param(request.query_params.get('days'),
                               default=30, minimum=1, maximum=365)
    except ValueError as e:
        return Response({'error': str(e)},
                        status=http_status.HTTP_400_BAD_REQUEST)
    # localdate, а не datetime.now(): при TIME_ZONE='Europe/Moscow' наивное
    # системное время сервера (обычно UTC) сдвигает границы суток на три
    # часа, и операции раннего утра попадают не в тот день.
    to_date = timezone.localdate()
    from_date = to_date - timedelta(days=days_back)
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
        'дата': str(timezone.localdate()),
        'позиций_в_дефиците': len(rows),
        'строки': rows,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def inventory_report(request):
    """Результаты инвентаризаций."""
    inventory_id = request.query_params.get('inventory')

    if inventory_id:
        try:
            inventory_id = int(inventory_id)
        except (TypeError, ValueError):
            return Response(
                {'error': f'Неверный номер инвентаризации: {inventory_id!r}'},
                status=http_status.HTTP_400_BAD_REQUEST)
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
        'дата': str(timezone.localdate()),
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
