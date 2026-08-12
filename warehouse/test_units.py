"""Свои единицы измерения.

Единиц было ровно пять, и жили они в коде: добавить пару, рулон или
упаковку без правки программы было нельзя. Предприятие считает в том, в
чём считает, — пары обуви записывали штуками и делили в уме, а это
рано или поздно ошибка в накладной.

Здесь проверяется, что новую единицу можно завести, что она сразу
годится для материалов, и что убрать из-под ног уже используемую
нельзя.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Material, Unit

User = get_user_model()


class UnitTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='glava', password='x', role='admin')
        self.client.force_login(self.user)


class BuiltinUnitsTest(UnitTestBase):
    """Пять прежних единиц никуда не делись."""

    def test_they_are_in_the_table(self):
        codes = set(Unit.objects.values_list('code', flat=True))
        self.assertTrue({'pc', 'kg', 'm', 'm2', 'l'} <= codes)

    def test_they_are_marked_builtin(self):
        self.assertTrue(Unit.objects.get(code='kg').builtin)

    def test_they_have_okei_codes(self):
        """Код по классификатору нужен в накладных и счетах-фактурах."""
        self.assertEqual(Unit.objects.get(code='kg').okei, '166')

    def test_builtin_cannot_be_deleted(self):
        unit = Unit.objects.get(code='kg')
        answer = self.client.delete(f'/api/units/{unit.pk}/')
        self.assertEqual(answer.status_code, 400)
        self.assertTrue(Unit.objects.filter(pk=unit.pk).exists())


class NewUnitTest(UnitTestBase):
    """Своя единица."""

    def add(self, **fields):
        data = {'code': 'pair', 'name': 'пара', 'full_name': 'Пара',
                'okei': '715'}
        data.update(fields)
        return self.client.post('/api/units/', data,
                                content_type='application/json')

    def test_can_be_added(self):
        answer = self.add()
        self.assertEqual(answer.status_code, 201)
        self.assertTrue(Unit.objects.filter(code='pair').exists())

    def test_new_unit_is_not_builtin(self):
        """Иначе своей единице можно объявить себя встроенной и обойти
        запрет на удаление."""
        self.add(builtin=True)
        self.assertFalse(Unit.objects.get(code='pair').builtin)

    def test_material_can_use_it(self):
        self.add()
        answer = self.client.post('/api/materials/', {
            'name': 'Стельки', 'unit': 'pair', 'category': 'leather',
            'reorder_point': '10'}, content_type='application/json')
        self.assertEqual(answer.status_code, 201)

    def test_material_shows_the_new_unit(self):
        self.add()
        material = Material.objects.create(
            name='Стельки', unit='pair', category='leather',
            reorder_point=Decimal('10'))
        self.assertEqual(material.get_unit_display(), 'пара')

        row = self.client.get(f'/api/materials/{material.pk}/').json()
        self.assertEqual(row['unit_display'], 'пара')

    def test_unknown_unit_is_refused(self):
        """Без списка выбора в поле прошла бы любая строка.

        Материал с единицей «шт» вместо «шт.» выглядит настоящим, а в
        отчётах живёт отдельной строкой.
        """
        answer = self.client.post('/api/materials/', {
            'name': 'Шнурки', 'unit': 'выдумка', 'category': 'textile',
            'reorder_point': '1'}, content_type='application/json')
        self.assertEqual(answer.status_code, 400)
        self.assertIn('единиц', str(answer.json()).lower())

    def test_duplicate_code_is_refused(self):
        self.add()
        self.assertEqual(self.add().status_code, 400)


class UsedUnitTest(UnitTestBase):
    """Единицу, по которой ведётся учёт, трогать нельзя."""

    def setUp(self):
        super().setUp()
        self.unit = Unit.objects.create(code='roll', name='рулон')
        self.material = Material.objects.create(
            name='Ткань подкладочная', unit='roll', category='textile',
            reorder_point=Decimal('5'))

    def test_cannot_be_deleted_while_used(self):
        answer = self.client.delete(f'/api/units/{self.unit.pk}/')
        self.assertEqual(answer.status_code, 400)
        self.assertIn('материал', answer.json()['error'])

    def test_can_be_deleted_when_free(self):
        self.material.delete()
        answer = self.client.delete(f'/api/units/{self.unit.pk}/')
        self.assertEqual(answer.status_code, 204)

    def test_code_cannot_be_changed(self):
        """Смена кода тихо оторвала бы от единицы все материалы."""
        answer = self.client.patch(
            f'/api/units/{self.unit.pk}/', {'code': 'rulon'},
            content_type='application/json')
        self.assertEqual(answer.status_code, 400)
        self.material.refresh_from_db()
        self.assertEqual(self.material.get_unit_display(), 'рулон')

    def test_name_can_be_corrected(self):
        answer = self.client.patch(
            f'/api/units/{self.unit.pk}/', {'name': 'рул.'},
            content_type='application/json')
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(Unit.objects.get(pk=self.unit.pk).name, 'рул.')

    def test_deleted_unit_does_not_blank_the_material(self):
        """Пустое место в накладной хуже непонятного кода."""
        Unit.objects.filter(pk=self.unit.pk).delete()
        self.assertEqual(self.material.get_unit_display(), 'roll')


class UnitAccessTest(UnitTestBase):
    """Кому можно править справочник."""

    def test_manager_cannot_add(self):
        """Справочники ведут экономист, кладовщик и администратор.

        Менеджер работает с заказами, номенклатуру он не заводит — и
        единицы измерения тоже часть номенклатуры.
        """
        self.client.logout()
        manager = User.objects.create_user(
            username='menedzher', password='x', role='manager')
        self.client.force_login(manager)
        answer = self.client.post('/api/units/', {'code': 'box', 'name': 'кор.'},
                                  content_type='application/json')
        self.assertEqual(answer.status_code, 403)

    def test_storekeeper_can_add(self):
        """Кладовщик номенклатуру ведёт — значит, и единицы тоже."""
        self.client.logout()
        worker = User.objects.create_user(
            username='klad', password='x', role='storekeeper')
        self.client.force_login(worker)
        answer = self.client.post('/api/units/', {'code': 'box', 'name': 'кор.'},
                                  content_type='application/json')
        self.assertEqual(answer.status_code, 201)

    def test_list_is_not_paginated(self):
        """Список короткий, а форма выбора берёт его целиком.

        С постраничной выдачей в форме молча оказались бы не все
        единицы.
        """
        answer = self.client.get('/api/units/')
        self.assertIsInstance(answer.json(), list)
