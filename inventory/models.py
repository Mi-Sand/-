"""
Модели инвентаризации.

Сущности «Инвентаризационная опись» и «Строка инвентаризации» из отчёта.
В строках фиксируются остаток по данным системы, фактический остаток по
результатам пересчёта и вычисленное расхождение.
"""
from django.conf import settings
from django.db import models

from warehouse.models import Material, Product, Warehouse


class Inventory(models.Model):
    """Инвентаризационная опись по складу."""

    STATUS_CHOICES = [
        ('draft', 'Черновик'),
        ('in_progress', 'Идёт пересчёт'),
        ('completed', 'Завершена'),
    ]

    number = models.CharField('Номер описи', max_length=50, unique=True)
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, verbose_name='Склад')
    status = models.CharField(
        'Статус', max_length=20, choices=STATUS_CHOICES, default='draft')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, verbose_name='Создал')
    created_at = models.DateTimeField('Создана', auto_now_add=True)
    completed_at = models.DateTimeField('Завершена', null=True, blank=True)

    class Meta:
        db_table = 'inventories'
        ordering = ['-created_at']
        verbose_name = 'Инвентаризация'
        verbose_name_plural = 'Инвентаризации'

    def __str__(self):
        return f'Опись № {self.number} ({self.warehouse})'

    @property
    def discrepancy_count(self):
        return sum(1 for i in self.items.all() if i.difference != 0)


class InventoryItem(models.Model):
    """Строка инвентаризации: система против факта."""

    inventory = models.ForeignKey(
        Inventory, on_delete=models.CASCADE, related_name='items')
    material = models.ForeignKey(
        Material, null=True, blank=True, on_delete=models.PROTECT)
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.PROTECT)
    system_quantity = models.DecimalField(
        'Остаток по системе', max_digits=12, decimal_places=2, default=0)
    actual_quantity = models.DecimalField(
        'Фактический остаток', max_digits=12, decimal_places=2,
        null=True, blank=True)

    class Meta:
        db_table = 'inventory_items'
        verbose_name = 'Строка инвентаризации'
        verbose_name_plural = 'Строки инвентаризации'

    def __str__(self):
        return f'{self.item_name}: {self.difference:+}'

    @property
    def item_name(self):
        return str(self.material or self.product or '—')

    @property
    def difference(self):
        """Расхождение: факт − система. Отрицательное — недостача."""
        if self.actual_quantity is None:
            return 0
        return self.actual_quantity - self.system_quantity
