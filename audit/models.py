"""Журнал действий пользователей.

Движения по складу и раньше писались с указанием пользователя. Не
хватало журнала по остальному: кто завёл материал, кто изменил цену,
кто отменил проведение документа. При расхождении в инвентаризации
разобраться было не с чем.

Запись журнала — свидетельство о прошлом, а не отражение настоящего.
Поэтому имя пользователя и название объекта хранятся здесь текстом,
а не только ссылкой: сотрудника увольняют, материал переименовывают,
документ удаляют — а запись «Иванов удалил приход № 14» должна
остаться читаемой и через год.
"""
from django.conf import settings
from django.db import models


class AuditEntry(models.Model):
    """Одно изменение: кто, когда, что и с чем."""

    class Action(models.TextChoices):
        CREATE = 'create', 'Создание'
        CHANGE = 'change', 'Изменение'
        DELETE = 'delete', 'Удаление'

    happened_at = models.DateTimeField(
        'Когда', auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='audit_entries',
        verbose_name='Кто')
    user_label = models.CharField(
        'Кто (текстом)', max_length=200, blank=True,
        help_text='Как звали пользователя на момент действия')
    action = models.CharField(
        'Действие', max_length=10, choices=Action.choices, db_index=True)

    model_label = models.CharField(
        'Что за запись', max_length=100, db_index=True,
        help_text='Например, warehouse.Material')
    model_title = models.CharField('Вид записи', max_length=100, blank=True)
    object_id = models.PositiveBigIntegerField('Номер записи', null=True)
    object_label = models.CharField(
        'Название записи', max_length=300, blank=True)

    #: {'поле': {'title': 'Цена', 'was': '100.00', 'now': '120.00'}}
    changes = models.JSONField('Что изменилось', default=dict, blank=True)

    class Meta:
        db_table = 'audit_entries'
        ordering = ['-happened_at', '-id']
        verbose_name = 'Запись журнала'
        verbose_name_plural = 'Журнал действий'
        indexes = [
            models.Index(fields=['model_label', 'object_id'],
                         name='audit_object_idx'),
        ]

    def __str__(self):
        who = self.user_label or 'система'
        return (f'{self.happened_at:%d.%m.%Y %H:%M} — {who}, '
                f'{self.get_action_display().lower()}: {self.object_label}')
