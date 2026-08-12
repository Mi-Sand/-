"""Печатные формы документов.

До этого приход, расход и заказ смотрелись только на экране: кладовщику
нечего было взять с собой на склад, а покупателю — вложить в коробку.

Форма одна на все три документа: шапка предприятия, таблица позиций,
итог и место для подписей. Отличаются они заголовком и набором строк
в шапке, и это лучше передать данными, чем тремя похожими шаблонами,
которые разойдутся при первой же правке.

Числа считаются здесь, а не в шаблоне: в шаблоне Django нет умножения,
и сумму строки пришлось бы собирать окольным путём.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from .models import InboundDocument, Order, OutboundDocument


def rows_of(items, name_of):
    """Позиции документа с посчитанной суммой строки."""
    rows = []
    for number, item in enumerate(items, start=1):
        rows.append({
            'number': number,
            'name': name_of(item),
            'unit': unit_of(item),
            'quantity': item.quantity,
            'price': getattr(item, 'unit_price', None) or getattr(
                item, 'price', 0),
            'sum': item.quantity * (getattr(item, 'unit_price', None)
                                    or getattr(item, 'price', 0)),
        })
    return rows


def unit_of(item):
    """Единица измерения: у материала своя, продукция считается штуками."""
    material = getattr(item, 'material', None)
    if material is not None:
        return material.get_unit_display()
    return 'шт.'


@login_required
def print_inbound(request, pk):
    document = get_object_or_404(
        InboundDocument.objects.select_related(
            'supplier', 'warehouse', 'created_by'),
        pk=pk)
    items = document.items.select_related('material', 'product').all()
    rows = rows_of(items, lambda item: item.item_name)
    return render(request, 'print_document.html', {
        'title': 'Приходная накладная',
        'number': document.doc_number,
        'date': document.doc_date,
        'processed': document.processed,
        'created_by': document.created_by,
        'head': [
            ('Поставщик', document.supplier),
            ('Склад', document.warehouse),
        ],
        'rows': rows,
        'total': sum(row['sum'] for row in rows),
        'signatures': [('Сдал', 'поставщик'), ('Принял', 'кладовщик')],
    })


@login_required
def print_outbound(request, pk):
    document = get_object_or_404(
        OutboundDocument.objects.select_related('warehouse', 'created_by'),
        pk=pk)
    items = document.items.select_related('material', 'product').all()
    rows = rows_of(items, lambda item: item.item_name)
    head = [
        ('Склад', document.warehouse),
        ('Назначение', document.get_purpose_display()),
    ]
    if document.production_order:
        head.append(('Производственный заказ', document.production_order))
    return render(request, 'print_document.html', {
        'title': 'Расходная накладная',
        'number': document.doc_number,
        'date': document.doc_date,
        'processed': document.processed,
        'created_by': document.created_by,
        'head': head,
        'rows': rows,
        'total': sum(row['sum'] for row in rows),
        'signatures': [('Отпустил', 'кладовщик'), ('Получил', '')],
    })


@login_required
def print_order(request, pk):
    """Лист комплектовщика: что собрать и кому отправить."""
    order = get_object_or_404(
        Order.objects.prefetch_related('items__product'), pk=pk)
    rows = rows_of(order.items.all(), lambda item: str(item.product))
    head = [
        ('Покупатель', order.customer_name),
        ('Телефон', order.customer_phone),
        ('Состояние', order.get_status_display()),
    ]
    if order.address:
        head.append(('Адрес доставки', order.address))
    if order.comment:
        head.append(('Комментарий', order.comment))
    return render(request, 'print_document.html', {
        'title': 'Заказ покупателя',
        'number': order.number,
        'date': order.created_at,
        # У заказа нет проведения: отметку в шапке показывать нечем.
        'processed': None,
        'created_by': None,
        'head': head,
        'rows': rows,
        'total': sum(row['sum'] for row in rows),
        'signatures': [('Собрал', 'кладовщик'), ('Выдал', '')],
    })


@login_required
def print_labels(request):
    """Лист этикеток со штрихкодами.

    Приёмка шла глазами и руками: кладовщик искал позицию в списке по
    названию. Со сканером он подносит прибор к коробке, и позиция
    находится сама — но для этого на коробке должна быть этикетка.

    Что печатать, задаётся в адресе:

        /print/labels/?material=3&count=12
        /print/labels/?product=7&product=8

    Позиции без штрихкода получают внутренний номер: покупать диапазон
    у регистратора нужно только тому, кто отдаёт товар в чужие
    магазины, а для своего склада хватает собственной нумерации.
    """
    from .barcode import BarcodeError, suggest
    from .barcode import svg as barcode_svg
    from .models import Material, Product
    from .params import read_id

    # Сколько одинаковых этикеток на позицию. Сотня — предел разумного:
    # больше на лист всё равно не влезет, а опечатка в адресе не должна
    # заставлять принтер печатать до утра.
    count = read_id(request.GET.get('count')) or 1
    count = min(count, 100)

    wanted = []
    for value in request.GET.getlist('material'):
        number = read_id(value)
        if number:
            wanted.append(('material', number))
    for value in request.GET.getlist('product'):
        number = read_id(value)
        if number:
            wanted.append(('product', number))

    labels = []
    for kind, number in wanted:
        if kind == 'material':
            item = Material.objects.filter(pk=number).first()
            if not item:
                continue
            code = item.barcode or suggest('MAT', item.pk)
            second = item.article_number or item.get_unit_display()
        else:
            item = Product.objects.filter(pk=number).first()
            if not item:
                continue
            code = item.barcode or suggest('PRD', item.pk)
            second = f'{item.article_number} · {item.size} · {item.color}'

        try:
            drawing = barcode_svg(code)
        except BarcodeError as error:
            # Одна негодная позиция не должна оставить кладовщика вовсе
            # без листа: печатаем остальные, а про эту говорим на самой
            # этикетке.
            drawing = None
            second = str(error)

        for _ in range(count):
            labels.append({
                'name': item.name,
                'second': second,
                'code': code,
                'drawing': drawing,
            })

    return render(request, 'print_labels.html', {
        'labels': labels,
        'count': count,
    })
