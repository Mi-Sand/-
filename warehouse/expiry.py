"""
Партии с истекающим сроком годности.

Срок годности и номер партии вводились в приходном документе с самого
начала, показывались в списке приходов — и на этом всё. Ни отчёта, ни
предупреждения: система знала, что клей просрочен, и молчала.

Здесь эти данные наконец используются.

Важное ограничение, о котором нужно знать
-----------------------------------------
Остатки в системе ведутся по позиции склада, а не по партиям: таблица
Stock хранит «столько-то ткани на таком-то складе» без разбивки на
поступления. Поэтому точно сказать, сколько единиц конкретной партии
осталось, нельзя — эти сведения в базе просто не хранятся.

Отчёт исходит из осторожного допущения: партия считается на складе, пока
общий остаток материала больше нуля. Это может завысить картину — если
материал давно израсходовали и завезли заново, старая партия всё равно
попадёт в список. Ошибка в безопасную сторону: лучше лишний раз проверить
полку, чем не заметить просроченное сырьё.

Полноценный учёт по партиям потребовал бы отдельной таблицы остатков с
привязкой к строке прихода и выбора партии при каждом списании. Это
заметно более крупная работа, и здесь она не делается.
"""
from datetime import timedelta

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import InboundItem, Stock

# За сколько дней до окончания срока партия попадает в список.
# Месяц — разумный запас: успеть израсходовать или вернуть поставщику.
DEFAULT_WARNING_DAYS = 30


def expiring_batches(days=DEFAULT_WARNING_DAYS, include_expired=True):
    """Партии, срок годности которых истёк или истекает в ближайшие дни.

    Учитываются только строки проведённых приходов: непроведённый документ
    товара на склад не положил, и предупреждать по нему не о чем.

    Возвращает список словарей, отсортированный по дате: сначала то, что
    уже просрочено, затем истекающее.
    """
    today = timezone.localdate()
    deadline = today + timedelta(days=days)

    items = (InboundItem.objects
             .filter(inbound_doc__processed=True,
                     expiry_date__isnull=False,
                     expiry_date__lte=deadline)
             .select_related('material', 'product',
                             'inbound_doc', 'inbound_doc__warehouse',
                             'inbound_doc__supplier')
             .order_by('expiry_date'))

    if not include_expired:
        items = items.filter(expiry_date__gte=today)

    # Остатки по всем позициям разом — чтобы не ходить в базу на каждую
    # строку прихода
    stock_by_material = _totals('material_id')
    stock_by_product = _totals('product_id')

    rows = []
    for item in items:
        if item.material_id:
            remaining = stock_by_material.get(item.material_id, 0)
        else:
            remaining = stock_by_product.get(item.product_id, 0)

        # Позиции, которых на складе не осталось вовсе, не показываем:
        # просроченная партия израсходованного материала — не новость
        if remaining <= 0:
            continue

        days_left = (item.expiry_date - today).days
        rows.append({
            'item_name': item.item_name,
            'batch_number': item.batch_number or '—',
            'expiry_date': item.expiry_date,
            'days_left': days_left,
            'expired': days_left < 0,
            'quantity_received': item.quantity,
            'stock_remaining': remaining,
            'warehouse': item.inbound_doc.warehouse.name,
            'supplier': item.inbound_doc.supplier.name,
            'document': item.inbound_doc.doc_number,
            'received_at': item.inbound_doc.doc_date,
        })
    return rows


def _totals(field):
    """Суммарные остатки, сгруппированные по материалу или продукции."""
    rows = (Stock.objects
            .filter(**{f'{field}__isnull': False})
            .values(field)
            .annotate(total=Coalesce(
                Sum('quantity'), Value(0, output_field=DecimalField()))))
    return {row[field]: row['total'] for row in rows}


def expiry_summary(days=DEFAULT_WARNING_DAYS):
    """Краткая сводка для главной панели: сколько просрочено и сколько истекает."""
    rows = expiring_batches(days=days)
    expired = [row for row in rows if row['expired']]
    return {
        'expired_count': len(expired),
        'expiring_count': len(rows) - len(expired),
        'total_count': len(rows),
        # Ближайшие пять — чтобы показать прямо на главной
        'items': rows[:5],
    }
