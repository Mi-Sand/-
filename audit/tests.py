"""Проверка журнала действий.

Журнал ценен ровно тем, что ему можно верить. Поэтому здесь проверяется
не столько «запись появилась», сколько то, ради чего журнал заводили:
что в нём видно старое и новое значение, что действие подписано тем, кто
его совершил, и что удалённая запись не уносит с собой след о себе.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from audit.current_user import (get_current_user, reset_current_user,
                                set_current_user)
from audit.models import AuditEntry
from warehouse.models import Material, Product, Supplier, Warehouse

User = get_user_model()


def make_material(**kwargs):
    values = dict(name='Кожа хромовая', unit='kg', category='leather',
                  reorder_point=Decimal('5.00'))
    values.update(kwargs)
    return Material.objects.create(**values)


def make_product(**kwargs):
    values = dict(article_number='А-100', name='Мяч футбольный',
                  category='equipment', size='5', color='белый',
                  cost=Decimal('300.00'), selling_price=Decimal('100.00'))
    values.update(kwargs)
    return Product.objects.create(**values)


class SignalTest(TestCase):
    """Что попадает в журнал при создании, правке и удалении."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='ivanov', password='x', first_name='Иван',
            last_name='Иванов', role='storekeeper')
        token = set_current_user(self.user)
        self.addCleanup(reset_current_user, token)
        AuditEntry.objects.all().delete()   # завод пользователя тоже пишется

    def last(self):
        return AuditEntry.objects.order_by('-id').first()

    def test_creation_is_recorded(self):
        material = make_material()
        entry = self.last()
        self.assertEqual(entry.action, AuditEntry.Action.CREATE)
        self.assertEqual(entry.object_id, material.pk)
        self.assertIn('Кожа хромовая', entry.object_label)
        self.assertEqual(entry.model_title, 'Материал')

    def test_change_keeps_old_and_new(self):
        """Ради этого всё и затевалось: с какой цены на какую."""
        product = make_product()
        product.selling_price = Decimal('120.00')
        product.save()

        entry = self.last()
        self.assertEqual(entry.action, AuditEntry.Action.CHANGE)
        self.assertIn('selling_price', entry.changes)
        self.assertEqual(entry.changes['selling_price']['was'], '100.00')
        self.assertEqual(entry.changes['selling_price']['now'], '120.00')

    def test_untouched_fields_are_not_listed(self):
        product = make_product()
        product.selling_price = Decimal('120.00')
        product.save()
        self.assertEqual(list(self.last().changes), ['selling_price'])

    def test_save_without_changes_writes_nothing(self):
        """Пересохранение без правок засоряло бы журнал."""
        material = make_material()
        before = AuditEntry.objects.count()
        material.save()
        self.assertEqual(AuditEntry.objects.count(), before)

    def test_deletion_keeps_what_was_deleted(self):
        material = make_material(name='Нитки капроновые')
        material.delete()

        entry = self.last()
        self.assertEqual(entry.action, AuditEntry.Action.DELETE)
        self.assertIn('Нитки капроновые', entry.object_label)
        self.assertEqual(entry.changes['name']['was'], 'Нитки капроновые')

    def test_action_is_signed_by_the_user(self):
        make_material()
        entry = self.last()
        self.assertEqual(entry.user, self.user)
        self.assertIn('Иванов', entry.user_label)

    def test_record_survives_the_user(self):
        """Сотрудника уволили — запись должна остаться читаемой.

        Ссылка на учётку обнулится, а имя останется текстом: иначе
        журнал терял бы ровно те записи, которые важнее всего.
        """
        make_material()
        self.user.delete()
        entry = AuditEntry.objects.filter(
            model_label='warehouse.Material').order_by('-id').first()
        self.assertIsNone(entry.user)
        self.assertIn('Иванов', entry.user_label)

    def test_choices_are_written_readably(self):
        """В журнале «Килограмм», а не «kg»."""
        make_material()
        self.assertEqual(self.last().changes['unit']['now'], 'кг')

    def test_links_are_written_by_name(self):
        """Ссылка разворачивается в название, а не в номер."""
        supplier = Supplier.objects.create(name='ООО Поставка')
        warehouse = Warehouse.objects.create(name='Основной', type='raw')
        from datetime import date

        from warehouse.models import InboundDocument
        document = InboundDocument.objects.create(
            doc_number='ПР-1', doc_date=date(2026, 1, 9), supplier=supplier,
            warehouse=warehouse, created_by=self.user)
        entry = AuditEntry.objects.filter(
            model_label='warehouse.InboundDocument',
            object_id=document.pk).first()
        self.assertEqual(entry.changes['supplier']['now'], 'ООО Поставка')

    def test_stock_churn_is_not_recorded(self):
        """Остатки в журнал не идут — иначе его нельзя будет читать."""
        warehouse = Warehouse.objects.create(name='Основной', type='raw')
        material = make_material()
        before = AuditEntry.objects.count()
        from warehouse.models import Stock
        Stock.objects.create(material=material, warehouse=warehouse,
                             quantity=Decimal('10'))
        self.assertEqual(AuditEntry.objects.count(), before)


class WithoutUserTest(TestCase):
    """Действия вне запроса: команда, планировщик, витрина."""

    def test_entry_is_written_without_a_user(self):
        make_material()
        entry = AuditEntry.objects.order_by('-id').first()
        self.assertIsNone(entry.user)
        self.assertEqual(entry.user_label, '')

    def test_user_is_not_left_behind(self):
        """Оставленный пользователь приписал бы себе чужие действия.

        Рабочие потоки живут долго и переиспользуются: не снятое
        значение однажды подписало бы ночное задание именем того, кто
        последним заходил вечером.
        """
        user = User.objects.create_user(username='petrov', password='x')
        token = set_current_user(user)
        reset_current_user(token)
        self.assertIsNone(get_current_user())


class MiddlewareTest(TestCase):
    """Пользователь берётся из запроса."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='sidorov', password='secret', role='admin',
            is_superuser=True, last_name='Сидоров')
        self.client.force_login(self.user)

    def test_change_through_the_site_is_signed(self):
        product = make_product()
        AuditEntry.objects.all().delete()

        # Запрос идёт через вход по сессии, а не force_authenticate:
        # пользователя журналу даёт middleware, а оно берёт его из
        # сессии. При подстановке пользователя на уровне обработчика
        # middleware видел бы гостя, и проверка ничего бы не значила.
        response = self.client.patch(
            f'/api/products/{product.pk}/', {'selling_price': '250.00'},
            content_type='application/json')
        self.assertEqual(response.status_code, 200)

        entry = AuditEntry.objects.order_by('-id').first()
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.changes['selling_price']['now'], '250.00')

    def test_page_opens(self):
        response = self.client.get(reverse('audit-page'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Журнал действий')


class ApiTest(TestCase):
    """Журнал читают, и только читают."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='petrova', password='x', role='storekeeper',
            last_name='Петрова')
        self.client.force_login(self.user)
        token = set_current_user(self.user)
        self.addCleanup(reset_current_user, token)
        AuditEntry.objects.all().delete()
        self.material = make_material()

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get('/api/audit/')
        self.assertIn(response.status_code, (401, 403))

    def test_list_returns_entries(self):
        response = self.client.get('/api/audit/')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()['count'], 1)

    def test_entries_cannot_be_deleted_or_edited(self):
        """Журнал, который можно подчистить, ничего не доказывает."""
        entry = AuditEntry.objects.first()
        self.assertEqual(
            self.client.delete(f'/api/audit/{entry.pk}/').status_code, 405)
        self.assertEqual(
            self.client.post('/api/audit/', {}).status_code, 405)

    def test_filter_by_action(self):
        self.material.reorder_point = Decimal('7.00')
        self.material.save()
        response = self.client.get('/api/audit/?action=change')
        actions = {row['action'] for row in response.json()['results']}
        self.assertEqual(actions, {'change'})

    def test_filter_by_kind(self):
        Warehouse.objects.create(name='Основной', type='raw')
        response = self.client.get('/api/audit/?model=warehouse.Warehouse')
        labels = {row['model_label'] for row in response.json()['results']}
        self.assertEqual(labels, {'warehouse.Warehouse'})

    def test_filter_by_user(self):
        response = self.client.get(f'/api/audit/?user={self.user.pk}')
        self.assertGreaterEqual(response.json()['count'], 1)
        empty = self.client.get('/api/audit/?user=999999')
        self.assertEqual(empty.json()['count'], 0)

    def test_filter_by_date(self):
        today = self.client.get('/api/audit/?since=2000-01-01')
        self.assertGreaterEqual(today.json()['count'], 1)
        future = self.client.get('/api/audit/?until=2000-01-01')
        self.assertEqual(future.json()['count'], 0)

    def test_broken_filters_do_not_break_the_page(self):
        """Мусор в отборе не должен ронять журнал."""
        for query in ('?user=abc', '?since=вчера', '?until=32-13-2020',
                      '?action=выдумка', '?model=нет.Такой',
                      '?user=99999999999999999999'):
            response = self.client.get(f'/api/audit/{query}')
            self.assertEqual(response.status_code, 200, query)

    def test_search_by_name(self):
        response = self.client.get('/api/audit/?search=Кожа')
        self.assertGreaterEqual(response.json()['count'], 1)
        self.assertEqual(
            self.client.get('/api/audit/?search=такого нет').json()['count'], 0)
