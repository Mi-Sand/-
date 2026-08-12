"""Корзина удалённых документов.

Раньше удаление было окончательным. Непроведённый документ удаляли —
и он исчезал вместе со строками; восстановить его было нельзя ничем,
кроме резервной копии за прошлую ночь. А удаляют обычно в спешке и не
тот, что собирались.

Теперь удаление — это пометка. Документ пропадает из списков и отчётов,
но лежит в корзине, откуда его возвращают одним нажатием. Через
`TRASH_KEEP_DAYS` дней команда `purgetrash` вычищает старое насовсем:
корзина не должна расти без предела.

Проведённые документы сюда не попадают вовсе — их и удалять нельзя,
сначала сторно.
"""
from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from .models import InboundDocument, OutboundDocument

#: Сколько дней держать удалённое, если в настройках не сказано иное
DEFAULT_KEEP_DAYS = 30

#: Что лежит в корзине. Ключ виден в адресах, менять его нельзя.
KINDS = {
    'inbound': (InboundDocument, 'Приход'),
    'outbound': (OutboundDocument, 'Расход'),
}


def keep_days():
    return int(getattr(settings, 'TRASH_KEEP_DAYS', DEFAULT_KEEP_DAYS))


def mark_deleted(document, user=None):
    """Пометить документ удалённым."""
    document.deleted_at = timezone.now()
    document.deleted_by = user if (user and user.is_authenticated) else None
    document.save(update_fields=['deleted_at', 'deleted_by'])
    return document


def restore(document):
    """Вернуть документ из корзины.

    Номер документа в системе один на всю базу. Пока документ лежал в
    корзине, тот же номер могли занять заново — тогда возвращать некуда,
    и лучше сказать об этом прямо, чем упасть на уровне базы.
    """
    model = type(document)
    taken = (model.objects
             .filter(doc_number=document.doc_number)
             .exclude(pk=document.pk)
             .exists())
    if taken:
        raise ValueError(
            f'Номер {document.doc_number} уже занят другим документом. '
            f'Переименуйте его, а потом верните этот из корзины.')

    document.deleted_at = None
    document.deleted_by = None
    document.save(update_fields=['deleted_at', 'deleted_by'])
    return document


def items(kind=None):
    """Что сейчас лежит в корзине — по обоим видам документов."""
    rows = []
    for key, (model, title) in KINDS.items():
        if kind and kind != key:
            continue
        # Число строк считается сразу по всем документам одним
        # запросом: отдельный подсчёт на каждый документ означал бы
        # столько обращений к базе, сколько документов в корзине.
        found = (model.all_objects
                 .filter(deleted_at__isnull=False)
                 .select_related('warehouse', 'deleted_by')
                 .annotate(line_count=Count('items'))
                 .order_by('-deleted_at'))
        for document in found:
            rows.append({
                'kind': key,
                'kind_title': title,
                'id': document.pk,
                'doc_number': document.doc_number,
                'doc_date': document.doc_date,
                'warehouse': document.warehouse.name,
                'deleted_at': document.deleted_at,
                'deleted_by': (document.deleted_by.get_full_name()
                               or document.deleted_by.username)
                              if document.deleted_by else '',
                'lines': document.line_count,
                'days_left': days_left(document),
            })
    rows.sort(key=lambda row: row['deleted_at'], reverse=True)
    return rows


def days_left(document):
    """Сколько дней осталось до окончательной очистки."""
    age = (timezone.now() - document.deleted_at).days
    return max(keep_days() - age, 0)


def purge(older_than_days=None):
    """Удалить насовсем то, что пролежало в корзине дольше срока."""
    days = keep_days() if older_than_days is None else older_than_days
    edge = timezone.now() - timezone.timedelta(days=days)
    removed = {}
    for key, (model, _) in KINDS.items():
        found = model.all_objects.filter(deleted_at__lt=edge)
        removed[key] = found.count()
        found.delete()
    return removed
