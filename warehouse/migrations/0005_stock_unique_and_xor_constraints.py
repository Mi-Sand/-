"""Правильные ограничения уникальности остатков.

Прежнее ограничение перечисляло три поля разом (склад, материал, продукция)
и потому не работало: у каждой строки один из столбцов пуст, а в SQL NULL
не равен NULL — база считала такие строки различными и пропускала дубли.

Перед установкой новых ограничений база приводится в порядок: дубли по
одной позиции склада сливаются в одну строку с суммарным количеством, а
строки без материала и без продукции удаляются — они ни к чему не
относятся и появиться могли только из-за отсутствия проверки.
"""
from django.db import migrations, models


def merge_duplicate_stock(apps, schema_editor):
    """Слить дубли остатков и убрать строки без товара."""
    Stock = apps.get_model('warehouse', 'Stock')

    # Строки, не относящиеся ни к материалу, ни к продукции
    Stock.objects.filter(material__isnull=True, product__isnull=True).delete()

    seen = {}
    for stock in Stock.objects.order_by('id').iterator():
        key = (stock.warehouse_id, stock.material_id, stock.product_id)
        first = seen.get(key)
        if first is None:
            seen[key] = stock
            continue
        # Дубль: количество переносим в первую строку, дубль удаляем
        first.quantity = first.quantity + stock.quantity
        first.save(update_fields=['quantity'])
        stock.delete()


def noop(apps, schema_editor):
    """Обратной операции нет: слитые строки не восстанавливаются."""


class Migration(migrations.Migration):

    dependencies = [
        ("warehouse", "0004_order_alter_product_photo_orderitem"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="stock",
            name="uniq_stock_position",
        ),
        migrations.RunPython(merge_duplicate_stock, noop),
        migrations.AddConstraint(
            model_name="stock",
            constraint=models.UniqueConstraint(
                condition=models.Q(("product__isnull", True)),
                fields=("warehouse", "material"),
                name="uniq_stock_material",
            ),
        ),
        migrations.AddConstraint(
            model_name="stock",
            constraint=models.UniqueConstraint(
                condition=models.Q(("material__isnull", True)),
                fields=("warehouse", "product"),
                name="uniq_stock_product",
            ),
        ),
        migrations.AddConstraint(
            model_name="stock",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("material__isnull", False), ("product__isnull", True)),
                    models.Q(("material__isnull", True), ("product__isnull", False)),
                    _connector="OR",
                ),
                name="stock_material_xor_product",
            ),
        ),
    ]
