"""
Бизнес-логика инвентаризации.

Создание описи по текущим остаткам склада и завершение инвентаризации с
расчётом расхождений (фрагмент 10 отчёта). По каждому расхождению
формируется корректировочная запись в журнале движения, а остаток
приводится к фактическому.
"""
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from warehouse.models import Stock, StockMovement

from .models import Inventory, InventoryItem


@transaction.atomic
def build_inventory_sheet(inventory_id):
    """Заполнить опись позициями склада с текущими остатками по системе."""
    inv = Inventory.objects.select_for_update().get(pk=inventory_id)

    # Завершённая опись — документ учёта: по ней уже проведены корректировки
    # и разосланы уведомления о недостачах. Перезаполнение стёрло бы
    # результаты пересчёта и вернуло документ в работу задним числом.
    if inv.status == 'completed':
        raise ValueError(
            'Инвентаризация уже завершена — опись изменить нельзя. '
            'Для повторного пересчёта создайте новую опись.')

    inv.items.all().delete()

    stocks = Stock.objects.filter(warehouse=inv.warehouse)
    for st in stocks:
        InventoryItem.objects.create(
            inventory=inv,
            material=st.material,
            product=st.product,
            system_quantity=st.quantity,
        )
    inv.status = 'in_progress'
    inv.save()
    return inv


@transaction.atomic
def finalize_inventory(inventory_id, user=None):
    """Завершить инвентаризацию: рассчитать и провести расхождения.

    Возвращает список расхождений. По каждому создаётся корректировка в
    журнале, остаток приводится к фактическому. При существенной недостаче
    отправляется уведомление (доработка п. 2.4.3).
    """
    inv = Inventory.objects.select_for_update().get(pk=inventory_id)
    if inv.status == 'completed':
        raise ValueError('Инвентаризация уже завершена')

    discrepancies = []
    for item in inv.items.all():
        if item.actual_quantity is None:
            continue
        diff = item.actual_quantity - item.system_quantity
        if diff != 0:
            discrepancies.append({
                'item': item.item_name,
                'system': item.system_quantity,
                'actual': item.actual_quantity,
                'difference': diff,
            })

            stock, _ = Stock.objects.select_for_update().get_or_create(
                warehouse=inv.warehouse,
                material=item.material,
                product=item.product,
                defaults={'quantity': 0})

            # К остатку применяется расхождение, а не фактическое число.
            # Между составлением описи и её завершением склад продолжает
            # работать: мог прийти товар или уйти отгрузка. Если записать
            # результат пересчёта поверх, эти движения молча пропадут.
            # Расхождение же остаётся верным независимо от того, что
            # случилось после пересчёта.
            new_quantity = stock.quantity + diff
            if new_quantity < 0:
                raise ValueError(
                    f'«{item.item_name}»: расхождение {diff:+g} уводит '
                    f'остаток в минус (сейчас на складе {stock.quantity:g}). '
                    f'Проверьте результат пересчёта.')
            stock.quantity = new_quantity
            stock.save()

            StockMovement.objects.create(
                warehouse=inv.warehouse,
                material=item.material,
                product=item.product,
                movement_type='adjust',
                # Со знаком: недостача — минус, излишек — плюс. По модулю
                # восстановить остаток на прошедшую дату было бы нельзя.
                quantity=diff,
                document_id=inv.pk,
                user=user or inv.created_by)

    inv.status = 'completed'
    inv.completed_at = timezone.now()
    inv.save()

    # Уведомление о существенных недостачах
    shortages = [d for d in discrepancies if d['difference'] < 0]
    if shortages:
        lines = '\n'.join(
            f"- {d['item']}: недостача {abs(d['difference'])}"
            for d in shortages)
        send_mail(
            subject=f'Недостачи по инвентаризации № {inv.number}',
            message=(f'По итогам инвентаризации на складе '
                     f'«{inv.warehouse}» выявлены недостачи:\n{lines}'),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.WAREHOUSE_MANAGER_EMAIL],
            fail_silently=True)

    return discrepancies
