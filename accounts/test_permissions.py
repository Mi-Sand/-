"""Проверка прав доступа по ролям.

Раньше роли были только полем в базе: свойства `can_edit_documents` и
подобные существовали, но ни одна проверка на них не ссылалась. Эти тесты
фиксируют, что теперь роль действительно что-то решает — и что при этом
никому не закрыли просмотр данных, нужных для работы.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from warehouse.models import (InboundDocument, Material, Order, OrderItem,
                              Product, Stock, Supplier,
                              Warehouse)

User = get_user_model()


class RoleTestBase(TestCase):
    """Общая обстановка: по сотруднику на каждую роль и немного данных."""

    def setUp(self):
        self.users = {}
        for role in ('admin', 'storekeeper', 'economist', 'manager'):
            self.users[role] = User.objects.create_user(
                username=role, password='x', role=role)

        self.warehouse = Warehouse.objects.create(name='Основной',
                                                  type='finished')
        self.supplier = Supplier.objects.create(name='Поставщик')
        self.material = Material.objects.create(
            name='Ткань', unit='m', category='textile',
            reorder_point=Decimal('10'))
        self.product = Product.objects.create(
            article_number='A-1', name='Ботинки', category='shoes',
            size='42', color='чёрный', cost=100, selling_price=200)
        Stock.objects.create(warehouse=self.warehouse, product=self.product,
                             quantity=50)

    def client_for(self, role):
        client = APIClient()
        client.force_authenticate(self.users[role])
        return client


class CatalogAccessTest(RoleTestBase):
    """Справочники: ведут экономист, кладовщик и администратор."""

    PAYLOAD = {'name': 'Кожа', 'unit': 'm2', 'category': 'leather',
               'reorder_point': '5.00'}

    def test_allowed_roles_can_create(self):
        for role in ('admin', 'storekeeper', 'economist'):
            with self.subTest(role=role):
                response = self.client_for(role).post(
                    '/api/materials/', {**self.PAYLOAD,
                                        'name': f'Кожа {role}'},
                    format='json')
                self.assertEqual(response.status_code, 201)

    def test_manager_cannot_create(self):
        response = self.client_for('manager').post(
            '/api/materials/', self.PAYLOAD, format='json')
        self.assertEqual(response.status_code, 403)

    def test_manager_cannot_delete(self):
        response = self.client_for('manager').delete(
            f'/api/materials/{self.material.id}/')
        self.assertEqual(response.status_code, 403)

    def test_everyone_can_read(self):
        """Просмотр справочников нужен всем — его не закрывали."""
        for role in self.users:
            with self.subTest(role=role):
                response = self.client_for(role).get('/api/materials/')
                self.assertEqual(response.status_code, 200)

    def test_refusal_explains_who_can(self):
        """Отказ должен подсказывать, к кому идти, а не просто запрещать."""
        response = self.client_for('manager').post(
            '/api/materials/', self.PAYLOAD, format='json')
        self.assertIn('экономист', str(response.data).lower())


class DocumentAccessTest(RoleTestBase):
    """Складские документы: кладовщик и администратор."""

    def payload(self, number):
        return {
            'doc_number': number, 'doc_date': '2026-01-01',
            'supplier': self.supplier.id, 'warehouse': self.warehouse.id,
            'items': [{'material': self.material.id, 'quantity': '5.00',
                       'unit_price': '100.00'}],
        }

    def test_storekeeper_and_admin_can_create(self):
        for role in ('admin', 'storekeeper'):
            with self.subTest(role=role):
                response = self.client_for(role).post(
                    '/api/inbound-documents/', self.payload(f'П-{role}'),
                    format='json')
                self.assertEqual(response.status_code, 201)

    def test_economist_cannot_create(self):
        """Экономист ведёт справочники, но документы не проводит."""
        response = self.client_for('economist').post(
            '/api/inbound-documents/', self.payload('П-1'), format='json')
        self.assertEqual(response.status_code, 403)

    def test_manager_cannot_create(self):
        response = self.client_for('manager').post(
            '/api/inbound-documents/', self.payload('П-2'), format='json')
        self.assertEqual(response.status_code, 403)

    def test_economist_cannot_process(self):
        """Проведение — тоже POST, и оно тоже под защитой."""
        doc = InboundDocument.objects.create(
            doc_number='П-9', doc_date='2026-01-01',
            supplier=self.supplier, warehouse=self.warehouse)
        doc.items.create(material=self.material, quantity=5, unit_price=100)
        response = self.client_for('economist').post(
            f'/api/inbound-documents/{doc.id}/process/')
        self.assertEqual(response.status_code, 403)
        doc.refresh_from_db()
        self.assertFalse(doc.processed)

    def test_storekeeper_can_process(self):
        doc = InboundDocument.objects.create(
            doc_number='П-10', doc_date='2026-01-01',
            supplier=self.supplier, warehouse=self.warehouse)
        doc.items.create(material=self.material, quantity=5, unit_price=100)
        response = self.client_for('storekeeper').post(
            f'/api/inbound-documents/{doc.id}/process/')
        self.assertEqual(response.status_code, 200)
        doc.refresh_from_db()
        self.assertTrue(doc.processed)

    def test_everyone_can_read_documents(self):
        for role in self.users:
            with self.subTest(role=role):
                response = self.client_for(role).get(
                    '/api/inbound-documents/')
                self.assertEqual(response.status_code, 200)


class ProductionAccessTest(RoleTestBase):
    """Производство меняет остатки — значит, права как у документов."""

    def payload(self):
        return {'product': self.product.id, 'quantity': 1,
                'product_warehouse': self.warehouse.id,
                'material_warehouse': self.warehouse.id, 'materials': []}

    def test_economist_denied(self):
        response = self.client_for('economist').post(
            '/api/produce/', self.payload(), format='json')
        self.assertEqual(response.status_code, 403)

    def test_storekeeper_allowed(self):
        response = self.client_for('storekeeper').post(
            '/api/produce/', self.payload(), format='json')
        self.assertEqual(response.status_code, 200)


class InventoryAccessTest(RoleTestBase):
    """Инвентаризация завершается изменением остатков."""

    def test_manager_cannot_create(self):
        response = self.client_for('manager').post(
            '/api/inventories/', {'number': 'И-1',
                                  'warehouse': self.warehouse.id},
            format='json')
        self.assertEqual(response.status_code, 403)

    def test_storekeeper_can_create(self):
        response = self.client_for('storekeeper').post(
            '/api/inventories/', {'number': 'И-2',
                                  'warehouse': self.warehouse.id},
            format='json')
        self.assertEqual(response.status_code, 201)


class OrderAccessTest(RoleTestBase):
    """Заказы: подтверждение — менеджеру, отгрузка — кладовщику."""

    def setUp(self):
        super().setUp()
        self.order = Order.objects.create(
            number='ЗАК-00001', customer_name='Иван Петров',
            customer_phone='+79991234567')
        OrderItem.objects.create(order=self.order, product=self.product,
                                 quantity=2, price=200)

    def test_manager_can_confirm(self):
        response = self.client_for('manager').post(
            f'/api/orders/{self.order.id}/confirm/')
        self.assertEqual(response.status_code, 200)

    def test_economist_cannot_confirm(self):
        response = self.client_for('economist').post(
            f'/api/orders/{self.order.id}/confirm/')
        self.assertEqual(response.status_code, 403)

    def test_manager_cannot_ship(self):
        """Отгрузка списывает товар — это складская операция."""
        response = self.client_for('manager').post(
            f'/api/orders/{self.order.id}/ship/',
            {'warehouse': self.warehouse.id}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_storekeeper_can_ship(self):
        response = self.client_for('storekeeper').post(
            f'/api/orders/{self.order.id}/ship/',
            {'warehouse': self.warehouse.id}, format='json')
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'shipped')

    def test_everyone_can_read_orders(self):
        for role in self.users:
            with self.subTest(role=role):
                response = self.client_for(role).get('/api/orders/')
                self.assertEqual(response.status_code, 200)


class ReportAccessTest(RoleTestBase):
    """Отчёты открыты всем ролям — так и было задумано в модели."""

    def test_all_roles_can_read_reports(self):
        for role in self.users:
            for endpoint in ('/api/reports/stock/', '/api/reports/reorder/',
                             '/api/dashboard/'):
                with self.subTest(role=role, endpoint=endpoint):
                    response = self.client_for(role).get(endpoint)
                    self.assertEqual(response.status_code, 200)


class UserManagementAccessTest(RoleTestBase):
    """Учётные записи: только администратор, включая просмотр."""

    def test_admin_can_list(self):
        response = self.client_for('admin').get('/api/users/')
        self.assertEqual(response.status_code, 200)

    def test_others_denied(self):
        for role in ('storekeeper', 'economist', 'manager'):
            with self.subTest(role=role):
                response = self.client_for(role).get('/api/users/')
                self.assertEqual(response.status_code, 403)


class SuperuserTest(RoleTestBase):
    """Суперпользователь проходит везде независимо от роли."""

    def test_superuser_with_restrictive_role(self):
        root = User.objects.create_user(
            username='root', password='x', role='manager',
            is_superuser=True, is_staff=True)
        client = APIClient()
        client.force_authenticate(root)

        response = client.post('/api/materials/', {
            'name': 'Резина', 'unit': 'kg', 'category': 'polymer',
            'reorder_point': '1.00'}, format='json')
        self.assertEqual(response.status_code, 201,
                         'Роль менеджера не должна ограничивать суперпользователя')


class AnonymousTest(RoleTestBase):
    """Без входа в систему закрыто всё, кроме витрины."""

    def test_api_closed(self):
        anonymous = APIClient()
        for endpoint in ('/api/materials/', '/api/orders/',
                         '/api/inbound-documents/', '/api/reports/stock/'):
            with self.subTest(endpoint=endpoint):
                response = anonymous.get(endpoint)
                self.assertIn(response.status_code, (401, 403))

    def test_shop_open(self):
        anonymous = APIClient()
        response = anonymous.get('/api/shop/products/')
        self.assertEqual(response.status_code, 200)
