"""Снятие резерва с заказов, которых никто не подтвердил.

    python manage.py expireorders           # посмотреть и отменить
    python manage.py expireorders --dry-run # только посмотреть

Товар при оформлении заказа не списывается, а резервируется: физически
он на складе, но обещан покупателю и другим недоступен. Если заказ так
и остался новым — покупатель передумал, ошибся номером, не отвечает на
звонки, — товар висит в резерве и не показывается на витрине.

Через сутки такой заказ отменяется, и товар возвращается покупателям
сам. Срок — не догма: он задаётся в settings.py, и его стоит подобрать
под то, как быстро на самом деле обзванивают заказы.

Отменяются только новые заказы. Подтверждённый ждёт отгрузки сколько
угодно: за ним стоит договорённость с покупателем, и снимать резерв по
часам здесь нельзя.

Запускается заданием в планировщике — тем же, что снимает копии, или
отдельным. Раз в час достаточно.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from warehouse.models import Order

#: Через сколько часов новый заказ считается брошенным
DEFAULT_HOURS = 24


class Command(BaseCommand):
    help = 'Отменить неподтверждённые заказы и вернуть товар на витрину'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours', type=int, default=None, metavar='ЧАСОВ',
            help=f'Через сколько часов снимать резерв '
                 f'(по умолчанию {DEFAULT_HOURS}).')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Показать, что было бы отменено, но ничего не менять.')
        parser.add_argument(
            '--quiet', action='store_true',
            help='Молчать, если отменять нечего. Для запуска по расписанию.')

    def handle(self, *args, **options):
        hours = options['hours']
        if hours is None:
            hours = getattr(settings, 'SHOP_ORDER_LIMITS', {}).get(
                'unconfirmed_hours', DEFAULT_HOURS)
        if hours <= 0:
            self.stdout.write(
                'Срок снятия резерва отключён (0 часов) — ничего не делаю.')
            return

        edge = timezone.now() - timedelta(hours=hours)
        stale = (Order.objects
                 .filter(status='new', created_at__lt=edge)
                 .order_by('created_at'))

        if not stale.exists():
            if not options['quiet']:
                self.stdout.write(
                    f'Неподтверждённых заказов старше {hours} ч нет.')
            return

        self.stdout.write('')
        self.stdout.write(
            f'  Заказы без подтверждения дольше {hours} ч:')

        cancelled = 0
        for order in stale:
            age = timezone.now() - order.created_at
            days = age.days
            when = f'{days} дн.' if days else f'{age.seconds // 3600} ч'
            self.stdout.write(
                f'    {order.number:12} {order.customer_name[:28]:30} '
                f'{when:>7}')
            if options['dry_run']:
                continue

            with transaction.atomic():
                # Резерв нигде не хранится отдельно — он считается по
                # заказам в состояниях «новый» и «подтверждён». Поэтому
                # смены состояния достаточно: товар возвращается на
                # витрину сам, без правки остатков.
                order.status = 'cancelled'
                note = (f'Отменён системой: не подтверждён за {hours} ч. '
                        f'Товар возвращён на витрину.')
                order.comment = (f'{order.comment}\n{note}'.strip()
                                 if order.comment else note)
                order.save(update_fields=['status', 'comment'])
            cancelled += 1

        self.stdout.write('')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING(
                f'  Проверка вхолостую: отменено бы {stale.count()} заказов.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'  Отменено заказов: {cancelled}. '
                f'Товар вернулся на витрину.'))
        self.stdout.write('')
