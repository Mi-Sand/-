"""
Сигналы системы.

Уведомление о низком остатке материала (фрагмент 19 отчёта): при падении
остатка ниже минимального ответственному сотруднику отправляется письмо.
В режиме разработки письмо выводится в консоль (см. EMAIL_BACKEND).
"""
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Stock


@receiver(pre_save, sender=Stock)
def remember_previous_quantity(sender, instance, **kwargs):
    """Запомнить прежнее количество строки остатка.

    Нужно, чтобы в post_save отличить пересечение порога от сохранения
    строки, которая и так была ниже минимума. После сохранения прежнее
    значение уже не прочитать.
    """
    if not instance.pk:
        instance._quantity_before_save = Decimal('0')
        return
    previous = (Stock.objects
                .filter(pk=instance.pk)
                .values_list('quantity', flat=True)
                .first())
    instance._quantity_before_save = previous or Decimal('0')


def _total_on_hand(material_id):
    """Суммарный остаток материала по всем складам предприятия."""
    return (Stock.objects
            .filter(material_id=material_id)
            .aggregate(total=Coalesce(
                Sum('quantity'),
                Value(0, output_field=DecimalField())))['total'])


def _notify_low_stock(material, total):
    """Отправить письмо о нехватке материала."""
    send_mail(
        subject=f'Низкий остаток: {material.name}',
        message=(
            f'Суммарный остаток «{material.name}» по всем складам '
            f'составляет {total} {material.get_unit_display()} — '
            f'ниже минимума {material.reorder_point}. Требуется закупка.'),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[settings.WAREHOUSE_MANAGER_EMAIL],
        fail_silently=True)


@receiver(post_save, sender=Stock)
def check_reorder_point(sender, instance, **kwargs):
    """Отправить уведомление, если остаток материала ниже минимума.

    Считается сумма по всем складам, а не остаток одной строки. Минимальный
    остаток задан у материала в целом, поэтому 60 единиц на одном складе и
    60 на другом при минимуме 100 — это не дефицит, и письмо здесь было бы
    ложной тревогой. Отчёты «Дефицит» и «Позиции для закупки» считают так же.

    Письмо уходит только в момент пересечения порога: суммарный остаток был
    не ниже минимума, а стал ниже. Иначе каждое сохранение строки — а при
    проведении документа их много — порождало бы письмо об одном и том же.

    Отправка отложена до фиксации транзакции: если операция сорвётся и
    откатится, письмо о несуществующем дефиците уже не уйдёт.
    """
    material = instance.material
    if not material:
        return

    total = _total_on_hand(material.pk)
    if total >= material.reorder_point:
        return

    # Каким был суммарный остаток до этого сохранения
    before = getattr(instance, '_quantity_before_save', Decimal('0'))
    previous_total = total - instance.quantity + before
    if previous_total < material.reorder_point:
        return  # дефицит уже был — повторно не тревожим

    transaction.on_commit(lambda: _notify_low_stock(material, total))
