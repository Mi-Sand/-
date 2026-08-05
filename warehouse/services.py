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
    from .models import (Material, ProductionMaterial, ProductionRun,
                         Product)

    quantity = Decimal(str(quantity))
    if quantity <= 0:
        raise ValueError('Количество продукции должно быть больше нуля')

    product = Product.objects.get(pk=product_id)

    # Документ выпуска: без него производство не оставляло следа, и
    # восстановить, из чего сделана партия, было нельзя
    run = ProductionRun.objects.create(
        number=_next_production_number(),
        product=product,
        quantity=quantity,
        product_warehouse_id=product_warehouse_id,
        material_warehouse_id=material_warehouse_id,
        planned_unit_cost=product.cost,
        created_by=user)

    consumed = []

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

        # Цена берётся из истории закупок — той самой, что до сих пор
        # только заполнялась и никем не читалась
        price, known = _last_purchase_price(mat_id)
        consumed.append(ProductionMaterial(
            run=run, material=material, quantity=mat_qty,
            unit_price=price, price_known=known))

    ProductionMaterial.objects.bulk_create(consumed)

    # Себестоимость: сумма стоимости израсходованного, делённая на выпуск
    material_cost = sum((line.quantity * line.unit_price
                         for line in consumed), Decimal('0'))
    run.material_cost = material_cost.quantize(Decimal('0.01'))
    run.unit_cost = (material_cost / quantity).quantize(Decimal('0.01'))
    run.pricing_complete = all(line.price_known for line in consumed)
    run.save(update_fields=['material_cost', 'unit_cost',
                            'pricing_complete'])

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

    return run


def _next_production_number():
    """Следующий свободный номер выпуска вида ВЫП-00001.

    Номер берётся от наибольшего уже выданного, а не от количества
    записей: после удаления выпуска его номер не должен выдаваться
    повторно.
    """
    from .models import ProductionRun

    last = (ProductionRun.objects
            .filter(number__startswith='ВЫП-')
            .order_by('-number')
            .values_list('number', flat=True)
            .first())
    next_number = 1
    if last:
        try:
            next_number = int(last.split('-', 1)[1]) + 1
        except (IndexError, ValueError):
            next_number = ProductionRun.objects.count() + 1
    return f'ВЫП-{next_number:05d}'


def _last_purchase_price(material_id):
    """Последняя закупочная цена материала.

    Возвращает пару (цена, известна ли она). Если материал ни разу не
    закупался — цены взяться неоткуда, и себестоимость по нему выйдет
    заниженной. Об этом честно сообщается вторым значением, а не
    маскируется нулём.
    """
    from decimal import Decimal

    from .models import PriceHistory

    price = (PriceHistory.objects
             .filter(material_id=material_id)
             .order_by('-recorded_at')
             .values_list('price', flat=True)
             .first())
    if price is None:
        return Decimal('0.00'), False
    return price, True


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
