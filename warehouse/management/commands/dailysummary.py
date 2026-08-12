"""Сводка за сутки на почту ответственному.

    python manage.py dailysummary            # отправить
    python manage.py dailysummary --dry-run  # показать письмо на экране

Раньше письма уходили на каждое событие: остаток упал ниже минимума —
письмо, инвентаризация нашла недостачу — письмо. При десятке материалов
это десяток писем в день, и ящик перестают читать. Тогда предупреждения
формально есть, а фактически их никто не видит — это хуже, чем их
отсутствие, потому что на них рассчитывают.

Здесь одно письмо в день. Сначала то, что требует действия: остатки
ниже минимума, истекающие сроки, заказы без подтверждения, копия базы.
Потом — что произошло за сутки. Если писать не о чем, письмо всё равно
уходит: «за сутки ничего не требует внимания» — это тоже сведения, а
молчание можно спутать со сломанной рассылкой.

Запускается заданием в планировщике, обычно утром.
"""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Sum
from django.utils import timezone

from warehouse.models import (InboundDocument, Order, OutboundDocument,
                              ProductionRun, Stock, Unit)

#: За какой срок собирать сводку, если не сказано иное
DEFAULT_HOURS = 24


class Command(BaseCommand):
    help = 'Отправить сводку за сутки ответственному сотруднику'

    def add_arguments(self, parser):
        parser.add_argument(
            '--to', default=None, metavar='АДРЕС',
            help='Кому отправить. По умолчанию — адрес из настроек '
                 '(WAREHOUSE_MANAGER_EMAIL). Можно перечислить через '
                 'запятую.')
        parser.add_argument(
            '--hours', type=int, default=DEFAULT_HOURS, metavar='ЧАСОВ',
            help=f'За какой срок собирать сводку '
                 f'(по умолчанию {DEFAULT_HOURS}).')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Показать письмо на экране и никому не отправлять.')
        parser.add_argument(
            '--quiet', action='store_true',
            help='Не выводить письмо в вывод команды. Для расписания.')

    def handle(self, *args, **options):
        hours = options['hours']
        if hours <= 0:
            raise CommandError('Срок сводки должен быть больше нуля часов.')

        since = timezone.now() - timedelta(hours=hours)
        subject, body = self.build(since, hours)

        if options['dry_run']:
            self.stdout.write('')
            self.stdout.write(f'Тема: {subject}')
            self.stdout.write('')
            self.stdout.write(body)
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                '  Письмо не отправлено: запуск вхолостую (--dry-run).'))
            return

        recipients = self.recipients(options['to'])
        if not recipients:
            raise CommandError(
                'Некому отправлять: не задан адрес получателя.\n'
                '    Впишите в .env строку '
                'WAREHOUSE_MANAGER_EMAIL=имя@предприятие.рф\n'
                '    или укажите адрес прямо: --to имя@предприятие.рф')

        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            # Здесь не fail_silently: сводку ставят в расписание, и
            # молча не уходящее письмо — ровно та беда, ради которой всё
            # это делается. Пусть задание падает и это видно.
            fail_silently=False)

        if not options['quiet']:
            self.stdout.write(self.style.SUCCESS(
                f'  Сводка отправлена: {", ".join(recipients)}'))

    # --- сбор письма ----------------------------------------------------
    @staticmethod
    def recipients(given):
        source = given or getattr(settings, 'WAREHOUSE_MANAGER_EMAIL', '')
        return [address.strip() for address in str(source).split(',')
                if address.strip()]

    def build(self, since, hours):
        """Собрать тему и текст письма."""
        attention = self.attention()
        happened = self.happened(since)

        today = timezone.localtime().strftime('%d.%m.%Y')
        if attention:
            subject = (f'Склад {today}: требует внимания — '
                       f'{len(attention)} пункт(ов)')
        else:
            subject = f'Склад {today}: всё спокойно'

        lines = [f'Сводка за последние {hours} ч.', '']

        if attention:
            lines.append('ТРЕБУЕТ ВНИМАНИЯ')
            lines.append('')
            for title, rows in attention:
                lines.append(f'  {title}')
                lines.extend(f'    {row}' for row in rows)
                lines.append('')
        else:
            lines.append('Ничего, что требовало бы вмешательства, нет.')
            lines.append('')

        lines.append('ЧТО ПРОИСХОДИЛО')
        lines.append('')
        lines.extend(f'  {row}' for row in happened)
        lines.append('')
        lines.append('—')
        lines.append('Письмо отправлено системой складского учёта '
                     'ООО Фирма «ЛЕКО».')
        lines.append('Сводка приходит раз в сутки и заменяет письма на '
                     'каждое событие.')

        return subject, '\n'.join(lines)

    def attention(self):
        """То, из-за чего письмо вообще стоит открывать."""
        blocks = []

        low = self.low_stock()
        if low:
            blocks.append(('Остаток ниже минимума:', low))

        expiring = self.expiring()
        if expiring:
            blocks.append(('Сроки годности на исходе:', expiring))

        waiting = self.waiting_orders()
        if waiting:
            blocks.append(('Заказы ждут подтверждения:', waiting))

        stale = self.backup_age()
        if stale:
            blocks.append(('Резервные копии:', stale))

        return blocks

    @staticmethod
    def low_stock():
        """Материалы, которых осталось меньше минимума.

        Считается по сумме остатков на всех складах: материал может
        лежать в двух местах, и по отдельности каждая кучка выглядит
        малой, хотя вместе их достаточно.
        """
        rows = []
        units = dict(Unit.objects.values_list('code', 'name'))
        totals = (Stock.objects
                  .filter(material__isnull=False)
                  .values('material_id', 'material__name',
                          'material__unit', 'material__reorder_point')
                  .annotate(total=Sum('quantity'))
                  .filter(total__lt=F('material__reorder_point'))
                  .order_by('material__name'))
        for row in totals:
            # Единицы заводят сами, поэтому берём их из справочника, а
            # не из списка в коде: своя единица иначе не показалась бы.
            unit = units.get(row['material__unit'], row['material__unit'])
            rows.append(
                f"{row['material__name']}: {row['total']:.2f} {unit} "
                f"(минимум {row['material__reorder_point']:.2f})")
        return rows

    @staticmethod
    def expiring(days=30):
        """Партии, у которых срок годности вот-вот кончится."""
        try:
            from warehouse.expiry import expiring_batches
        except ImportError:                              # pragma: no cover
            return []

        rows = []
        for batch in expiring_batches(days)[:10]:
            left = batch['days_left']
            when = ('просрочено' if batch['expired']
                    else f'осталось {left} дн.')
            rows.append(
                f"{batch['item_name']}: партия {batch['batch_number']}, "
                f"до {batch['expiry_date']:%d.%m.%Y} — {when}, "
                f"на складе {batch['stock_remaining']:.2f}")
        return rows

    @staticmethod
    def waiting_orders():
        """Заказы, которые никто не подтвердил дольше суток.

        Пока заказ висит новым, товар в нём обещан покупателю и на
        витрине не показывается.
        """
        edge = timezone.now() - timedelta(hours=24)
        waiting = (Order.objects
                   .filter(status='new', created_at__lt=edge)
                   .order_by('created_at')[:10])
        return [f'{order.number} от {order.customer_name} '
                f'({order.created_at:%d.%m %H:%M})' for order in waiting]

    @staticmethod
    def backup_age(days=2):
        """Давно ли снимали копию базы."""
        root = Path(settings.BASE_DIR) / 'backups'
        if not root.is_dir():
            return ['копий нет вовсе — настройте ежедневное копирование']

        from warehouse.management.commands.backup import STAMP_FORMAT
        from datetime import datetime

        moments = []
        for path in root.iterdir():
            if not path.is_dir():
                continue
            if not any(path.glob('*.sqlite3')) and not any(path.glob('*.sql')):
                continue
            try:
                moments.append(datetime.strptime(path.name[:19], STAMP_FORMAT))
            except ValueError:
                continue

        if not moments:
            return ['копий нет вовсе — настройте ежедневное копирование']

        last = max(moments)
        age = (datetime.now() - last).days
        if age >= days:
            return [f'последняя копия {last:%d.%m.%Y %H:%M} — {age} дн. назад']
        return []

    def happened(self, since):
        """Что сделали за сутки. Здесь ничего тревожного — только счёт."""
        rows = []

        inbound = InboundDocument.objects.filter(
            created_at__gte=since, processed=True)
        rows.append(f'Приходов проведено: {inbound.count()}')

        outbound = OutboundDocument.objects.filter(
            created_at__gte=since, processed=True)
        rows.append(f'Расходов проведено: {outbound.count()}')

        runs = ProductionRun.objects.filter(created_at__gte=since)
        made = runs.aggregate(total=Sum('quantity'))['total'] or 0
        rows.append(f'Выпусков продукции: {runs.count()}'
                    + (f', изделий {made:.0f}' if made else ''))

        orders = Order.objects.filter(created_at__gte=since)
        rows.append(f'Заказов с витрины: {orders.count()}')

        shipped = Order.objects.filter(status='shipped',
                                       outbound_document__isnull=False,
                                       outbound_document__created_at__gte=since)
        rows.append(f'Заказов отгружено: {shipped.count()}')

        return rows
