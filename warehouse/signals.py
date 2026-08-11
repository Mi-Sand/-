"""
Сигналы системы.

Уведомление о низком остатке материала (фрагмент 19 отчёта): при падении
остатка ниже минимального ответственному сотруднику отправляется письмо.
В режиме разработки письмо выводится в консоль (см. EMAIL_BACKEND).
"""
from django.conf import settings
from django.core.mail import send_mail
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Stock


@receiver(post_save, sender=Stock)
def check_reorder_point(sender, instance, **kwargs):
    """Отправить уведомление, если остаток материала ниже минимума.

    По умолчанию письмо не уходит: остаток пересчитывается при каждом
    проведении документа, и при десятке материалов набегает десяток
    писем в день. Ящик с такой рассылкой перестают читать — и тогда
    предупреждения формально есть, а фактически их никто не видит.
    Это хуже их отсутствия, потому что на них рассчитывают.

    Вместо этого раз в сутки приходит сводка (`manage.py dailysummary`),
    и низкие остатки перечислены в ней первым же разделом.

    Кому письмо на каждое событие всё же нужно — включается в .env
    строкой NOTIFY_LOW_STOCK_INSTANTLY=True.
    """
    if not getattr(settings, 'NOTIFY_LOW_STOCK_INSTANTLY', False):
        return

    material = instance.material
    if material and instance.quantity < material.reorder_point:
        send_mail(
            subject=f'Низкий остаток: {material.name}',
            message=(
                f'Остаток «{material.name}» составляет '
                f'{instance.quantity} {material.get_unit_display()} — '
                f'ниже минимума {material.reorder_point}. '
                f'Склад: {instance.warehouse}. Требуется закупка.'),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.WAREHOUSE_MANAGER_EMAIL],
            fail_silently=True)
