"""Очистка корзины удалённых документов.

    python manage.py purgetrash
    python manage.py purgetrash --days 7 --yes

Корзина держит удалённые документы, чтобы их можно было вернуть. Но
держать их вечно — значит хранить в базе мусор, который никто уже не
востребует: за месяц становится ясно, что документ удалили не по
ошибке.

Команду ставят в то же ночное расписание, что и снятие копий.
"""
from django.core.management.base import BaseCommand

from warehouse.trash import KINDS, keep_days, purge


class Command(BaseCommand):
    help = 'Вычистить из корзины документы старше срока хранения'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=None, metavar='ДНЕЙ',
            help='Срок хранения. По умолчанию — из настроек '
                 '(TRASH_KEEP_DAYS).')
        parser.add_argument(
            '--yes', action='store_true',
            help='Не спрашивать подтверждения. Для расписания.')
        parser.add_argument(
            '--quiet', action='store_true',
            help='Молча. Для расписания.')

    def handle(self, *args, **options):
        days = options['days'] if options['days'] is not None else keep_days()

        from django.utils import timezone
        edge = timezone.now() - timezone.timedelta(days=days)
        doomed = {key: model.all_objects.filter(deleted_at__lt=edge).count()
                  for key, (model, _) in KINDS.items()}
        total = sum(doomed.values())

        if not total:
            if not options['quiet']:
                self.stdout.write(
                    f'  В корзине нет документов старше {days} дн.')
            return

        if not options['yes']:
            # Через input спрашивать нельзя: команду ставят в
            # расписание, и там ожидание ответа выглядит как зависшее
            # задание.
            self.stdout.write('')
            self.stdout.write(f'  Будет удалено насовсем: {total} шт.')
            for key, (_, title) in KINDS.items():
                if doomed[key]:
                    self.stdout.write(f'    {title}: {doomed[key]}')
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                '  Ничего не удалено. Повторите с ключом --yes, если '
                'согласны.'))
            return

        removed = purge(older_than_days=days)
        if not options['quiet']:
            self.stdout.write(self.style.SUCCESS(
                f'  Вычищено насовсем: {sum(removed.values())} шт. '
                f'(старше {days} дн.)'))
