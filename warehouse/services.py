"""
Бизнес-логика складских операций.

Здесь сосредоточена обработка приходных и расходных документов (фрагменты
8 и 9 отчёта). Каждая операция выполняется в одной транзакции с блокировкой
изменяемых строк остатков (select_for_update), что исключает
рассогласование данных при одновременной работе нескольких пользователей и
появление отрицательных остатков.
"""
from django.db import transaction

from .models import (InboundDocument, OutboundDocument, PriceHistory, Stock,
                     StockMovement)


class InsufficientStockError(Exception):
    """Возбуждается при попытке списать больше товара, чем есть на складе."""


@transaction.atomic
def process_inbound_document(doc_id, user=None):
    """Провести приходный документ: увеличить остатки и записать движение.

    Фрагмент 8 отчёта. Возвращает обработанный документ.
    """
    doc = (InboundDocument.objects
           .select_for_update()
           .get(pk=doc_id))

    if doc.processed:
        raise ValueError('Документ уже обработан')

    for item in doc.items.all():
        stock, _ = (Stock.objects
                    .select_for_update()
                    .get_or_create(
                        warehouse=doc.warehouse,
                        material=item.material,
                        product=item.product,
                        defaults={'quantity': 0}))
        stock.quantity += item.quantity
        stock.save()

        StockMovement.objects.create(
            warehouse=doc.warehouse,
            material=item.material,
            product=item.product,
            movement_type='in',
            quantity=item.quantity,
            document_id=doc.pk,
            user=user or doc.created_by)

        # Сохранение истории закупочной цены (доработка п. 2.4.3)
        PriceHistory.objects.create(
            material=item.material,
            product=item.product,
            price=item.unit_price,
            supplier=doc.supplier)

    doc.processed = True
    doc.save()
    return doc


@transaction.atomic
def process_outbound_document(doc_id, user=None):
    """Провести расходный документ: проверить и списать остатки.

    Фрагмент 9 отчёта. Перед списанием проверяется достаточность остатка;
    при нехватке операция прерывается с понятным сообщением, и никакие
    изменения не сохраняются (откат транзакции).
    """
    doc = (OutboundDocument.objects
           .select_for_update()
           .get(pk=doc_id))

    if doc.processed:
        raise ValueError('Документ уже обработан')

    for item in doc.items.all():
        try:
            stock = (Stock.objects
                     .select_for_update()
                     .get(warehouse=doc.warehouse,
                          material=item.material,
                          product=item.product))
        except Stock.DoesNotExist:
            raise InsufficientStockError(
                f'Товара «{item.item_name}» нет на складе '
                f'«{doc.warehouse}»')

        if stock.quantity < item.quantity:
            raise InsufficientStockError(
                f'Недостаточно товара «{item.item_name}»: '
                f'остаток {stock.quantity}, требуется {item.quantity}')

        stock.quantity -= item.quantity
        stock.save()

        StockMovement.objects.create(
            warehouse=doc.warehouse,
            material=item.material,
            product=item.product,
            movement_type='out',
            quantity=item.quantity,
            document_id=doc.pk,
            user=user or doc.created_by)

    doc.processed = True
    doc.save()
    return doc


@transaction.atomic
def produce_product(product_id, quantity, product_warehouse_id,
                    materials, material_warehouse_id, user=None):
    """Произвести продукцию из материалов.

    В одной транзакции:
    1. Проверяет и списывает материалы со склада сырья.
    2. Приходует готовую продукцию на склад продукции.
    3. Записывает все движения в журнал.

    Параметры:
        product_id — id продукции, которую производим;
        quantity — сколько единиц продукции произвели;
        product_warehouse_id — склад, куда приходуем продукцию;
        materials — список [{'material': id, 'quantity': N}, ...] —
                    какие материалы и сколько списываем;
        material_warehouse_id — склад, откуда списываем материалы.

    При нехватке любого материала операция полностью откатывается.
    """
    from decimal import Decimal
    from .models import Material, Product

    quantity = Decimal(str(quantity))
    if quantity <= 0:
        raise ValueError('Количество продукции должно быть больше нуля')

    product = Product.objects.get(pk=product_id)

    # 1. Списываем материалы (с проверкой достаточности)
    for row in materials:
        mat_id = row['material']
        mat_qty = Decimal(str(row['quantity']))
        if mat_qty <= 0:
            continue
        material = Material.objects.get(pk=mat_id)
        try:
            stock = (Stock.objects
                     .select_for_update()
                     .get(warehouse_id=material_warehouse_id,
                          material_id=mat_id, product__isnull=True))
        except Stock.DoesNotExist:
            raise InsufficientStockError(
                f'Материала «{material.name}» нет на складе сырья')
        if stock.quantity < mat_qty:
            raise InsufficientStockError(
                f'Недостаточно материала «{material.name}»: '
                f'остаток {stock.quantity}, требуется {mat_qty}')
        stock.quantity -= mat_qty
        stock.save()
        StockMovement.objects.create(
            warehouse_id=material_warehouse_id, material_id=mat_id,
            movement_type='out', quantity=mat_qty, user=user)

    # 2. Приходуем готовую продукцию
    stock, _ = (Stock.objects
                .select_for_update()
                .get_or_create(
                    warehouse_id=product_warehouse_id,
                    product_id=product_id, material__isnull=True,
                    defaults={'quantity': 0}))
    stock.quantity += quantity
    stock.save()
    StockMovement.objects.create(
        warehouse_id=product_warehouse_id, product_id=product_id,
        movement_type='in', quantity=quantity, user=user)

    return product


@transaction.atomic
def unprocess_inbound_document(doc_id, user=None):
    """Отменить проведение приходного документа (сторно).

    Откатывает то, что сделало проведение: списывает со склада ранее
    оприходованное количество и снимает отметку «проведён».

    Если товар уже израсходован и остатка не хватает для отката — операция
    отклоняется, чтобы не увести остаток в минус.
    """
    doc = (InboundDocument.objects
           .select_for_update()
           .prefetch_related('items')
           .get(pk=doc_id))

    if not doc.processed:
        raise ValueError('Документ не проведён — отменять нечего')

    for item in doc.items.all():
        try:
            stock = (Stock.objects
                     .select_for_update()
                     .get(warehouse=doc.warehouse,
                          material=item.material,
                          product=item.product))
        except Stock.DoesNotExist:
            raise InsufficientStockError(
                f'Не найден остаток «{item.item_name}» для отката')

        if stock.quantity < item.quantity:
            raise InsufficientStockError(
                f'Нельзя отменить: «{item.item_name}» частично израсходован '
                f'(на складе {stock.quantity}, нужно вернуть {item.quantity})')

        stock.quantity -= item.quantity
        stock.save()

        StockMovement.objects.create(
            warehouse=doc.warehouse,
            material=item.material,
            product=item.product,
            movement_type='out',
            quantity=item.quantity,
            user=user)

    doc.processed = False
    doc.save()
    return doc


@transaction.atomic
def unprocess_outbound_document(doc_id, user=None):
    """Отменить проведение расходного документа (сторно).

    Возвращает на склад ранее списанное количество и снимает отметку
    «проведён». Обратная операция всегда возможна: возврат только
    увеличивает остаток.
    """
    doc = (OutboundDocument.objects
           .select_for_update()
           .prefetch_related('items')
           .get(pk=doc_id))

    if not doc.processed:
        raise ValueError('Документ не проведён — отменять нечего')

    for item in doc.items.all():
        stock, _ = (Stock.objects
                    .select_for_update()
                    .get_or_create(
                        warehouse=doc.warehouse,
                        material=item.material,
                        product=item.product,
                        defaults={'quantity': 0}))
        stock.quantity += item.quantity
        stock.save()

        StockMovement.objects.create(
            warehouse=doc.warehouse,
            material=item.material,
            product=item.product,
            movement_type='in',
            quantity=item.quantity,
            user=user)

    doc.processed = False
    doc.save()
    return doc
