"""Заполнение журнала действий.

Журнал пишется сигналами, а не правкой каждого обработчика: так он
охватывает и то, что меняют из административной панели Django или из
командной строки, и не разойдётся с кодом, когда появится новый способ
завести материал.

Под наблюдением — справочники и документы. Остатки и движения сюда не
попадают намеренно: движение и так хранит пользователя, а остаток
меняется при каждом проведении, и журнал за неделю превратился бы в
поток строк, в котором ничего не найти. Журнал ценен ровно настолько,
насколько его можно прочитать.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .current_user import get_current_user
from .models import AuditEntry

#: За чем следим: путь к модели -> как называть её в журнале.
#: Строками, а не классами, — модуль загружается до готовности приложений.
WATCHED = {
    'warehouse.Material': 'Материал',
    'warehouse.Product': 'Продукция',
    'warehouse.Warehouse': 'Склад',
    'warehouse.Supplier': 'Поставщик',
    'warehouse.InboundDocument': 'Приход',
    'warehouse.OutboundDocument': 'Расход',
    'warehouse.Order': 'Заказ',
    'warehouse.ProductionRun': 'Выпуск продукции',
    'inventory.Inventory': 'Инвентаризация',
    'accounts.User': 'Сотрудник',
    # Деньги и реквизиты: правка задним числом здесь дороже всего.
    # Смена расчётного счёта в реквизитах уходит прямо в счёт
    # покупателю, а поправленная оплата меняет акт сверки.
    'billing.CompanyRequisites': 'Реквизиты организации',
    'billing.Counterparty': 'Покупатель-организация',
    'billing.Payment': 'Оплата',
}

#: Поля, которые в журнале только мешают: служебные отметки времени и
#: всё, что относится к паролю. Хранить хеш пароля в журнале — значит
#: аккуратно разложить его во втором месте.
IGNORED_FIELDS = {
    'created_at', 'updated_at', 'modified_at',
    'password', 'last_login', 'session_key',
}

#: Куда записи не помещаются целиком
LABEL_LIMIT = 300
VALUE_LIMIT = 200


def label_of(model):
    return f'{model._meta.app_label}.{model.__name__}'


def is_watched(model):
    return label_of(model) in WATCHED


def render(instance, field):
    """Значение поля строкой — так, как его прочтёт человек.

    Ссылки разворачиваются в название, а не в номер: «Кожа хромовая»
    вместо «17». Номер через полгода не скажет ничего, а название
    останется понятным, даже если запись потом удалят.
    """
    name = field.name
    if field.choices:
        getter = getattr(instance, f'get_{name}_display', None)
        if getter:
            return short(getter())
    value = getattr(instance, field.attname if field.is_relation else name,
                    None)
    if field.is_relation:
        if value is None:
            return ''
        # Сначала — уже загруженный объект: связанные записи почти
        # всегда есть в кеше, и лишнего запроса не будет.
        related = field.get_cached_value(instance, default=None)
        if related is None:
            try:
                related = getattr(instance, name)
            except ObjectDoesNotExist:
                # Связанную запись успели удалить — остаётся номер.
                return f'№{value}'
        return short(str(related))
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'да' if value else 'нет'
    if isinstance(value, Decimal):
        return f'{value:f}'
    return short(str(value))


def short(text, limit=VALUE_LIMIT):
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def snapshot(instance):
    """Значения полей записи: {поле: (заголовок, значение)}."""
    result = {}
    for field in instance._meta.concrete_fields:
        if field.name in IGNORED_FIELDS or field.primary_key:
            continue
        result[field.name] = (str(field.verbose_name), render(instance, field))
    return result


def write(instance, action, changes):
    """Положить запись в журнал.

    Сбой записи журнала не должен ронять само действие: пользователь
    оформляет приход, а не ведёт журнал. Поэтому исключение здесь
    гасится — но не молча: оно уходит в журнал ошибок Django, иначе
    пропажу записей никто не заметит.
    """
    user = get_current_user()
    # Сотрудник, удаляющий собственную учётную запись, ссылкой на себя
    # больше не годится: его строки в базе уже нет, и запись журнала
    # указывала бы в пустоту. Имя при этом сохраняется текстом — след
    # об удалении остаётся.
    link = user
    if user is not None and action == AuditEntry.Action.DELETE \
            and label_of(type(instance)) == settings.AUTH_USER_MODEL \
            and instance.pk == getattr(user, 'pk', None):
        link = None
    try:
        AuditEntry.objects.create(
            user=link if link is not None and link.pk else None,
            user_label=short(str(user), 200) if user is not None else '',
            action=action,
            model_label=label_of(type(instance)),
            model_title=WATCHED.get(label_of(type(instance)), ''),
            object_id=instance.pk if isinstance(instance.pk, int) else None,
            object_label=short(str(instance), LABEL_LIMIT),
            changes=changes)
    except Exception:                                    # pragma: no cover
        import logging
        logging.getLogger('audit').exception(
            'Не удалось записать действие в журнал')


# --- подписки ---------------------------------------------------------
@receiver(pre_save)
def remember_previous(sender, instance, raw=False, **kwargs):
    """Запомнить, как запись выглядела до сохранения.

    После сохранения прежних значений уже не достать, а без них
    журнал скажет лишь «цену меняли» — но не с какой на какую.
    """
    if raw or not is_watched(sender) or not instance.pk:
        return
    previous = sender.objects.filter(pk=instance.pk).first()
    instance._audit_before = snapshot(previous) if previous else None


@receiver(post_save)
def record_save(sender, instance, created, raw=False, **kwargs):
    if raw or not is_watched(sender):
        return

    now = snapshot(instance)
    if created:
        changes = {name: {'title': title, 'was': '', 'now': value}
                   for name, (title, value) in now.items() if value != ''}
        write(instance, AuditEntry.Action.CREATE, changes)
        return

    before = getattr(instance, '_audit_before', None)
    if before is None:
        return
    changes = {}
    for name, (title, value) in now.items():
        was = before.get(name, (title, ''))[1]
        if was != value:
            changes[name] = {'title': title, 'was': was, 'now': value}
    # Сохранение без единой правки — обычное дело: обработчик мог
    # записать объект целиком, ничего в нём не поменяв. Такие строки
    # только засоряют журнал.
    if changes:
        write(instance, AuditEntry.Action.CHANGE, changes)


@receiver(post_delete)
def record_delete(sender, instance, **kwargs):
    if not is_watched(sender):
        return
    changes = {name: {'title': title, 'was': value, 'now': ''}
               for name, (title, value) in snapshot(instance).items()
               if value != ''}
    write(instance, AuditEntry.Action.DELETE, changes)
