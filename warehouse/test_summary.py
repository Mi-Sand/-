"""Проверка ежедневной сводки и проверочного письма.

Сводка — единственное, что связывает систему с человеком, когда он в
неё не заходит. Если в ней окажется неправда или она перестанет
уходить, узнают об этом позже всего.
"""
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (InboundDocument, InboundItem, Material, Order, Product,
                     Stock, Warehouse)

User = get_user_model()


class SummaryTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='klad', password='x')
        self.warehouse = Warehouse.objects.create(name='Основной', type='raw')
        self.material = Material.objects.create(
            name='Кожа хромовая', unit='m', category='leather',
            reorder_point=Decimal('50'))
        self.product = Product.objects.create(
            article_number='МЯ-1', name='Мяч футбольный', category='equipment',
            size='5', color='белый', cost=Decimal('300'),
            selling_price=Decimal('1500'))

    def letter(self, **options):
        out = StringIO()
        call_command('dailysummary', stdout=out, stderr=out, **options)
        return out.getvalue()


class SummaryContentTest(SummaryTestBase):
    """Что попадает в письмо."""

    def test_low_stock_is_the_first_thing_seen(self):
        Stock.objects.create(warehouse=self.warehouse, material=self.material,
                             quantity=Decimal('7'))
        text = self.letter(dry_run=True)
        self.assertIn('ТРЕБУЕТ ВНИМАНИЯ', text)
        self.assertIn('Кожа хромовая', text)
        self.assertIn('минимум 50', text)

    def test_stock_is_counted_across_warehouses(self):
        """Материал может лежать в двух местах.

        По отдельности каждая кучка меньше минимума, а вместе их
        достаточно — письмо о нехватке было бы неправдой.
        """
        second = Warehouse.objects.create(name='Запасной', type='raw')
        Stock.objects.create(warehouse=self.warehouse, material=self.material,
                             quantity=Decimal('30'))
        Stock.objects.create(warehouse=second, material=self.material,
                             quantity=Decimal('40'))
        text = self.letter(dry_run=True)
        self.assertNotIn('Кожа хромовая', text)

    def test_orders_waiting_for_confirmation(self):
        order = Order.objects.create(
            number='З-1', customer_name='Иванов Иван',
            customer_phone='+79990000000', status='new')
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(hours=30))
        text = self.letter(dry_run=True)
        self.assertIn('ждут подтверждения', text)
        self.assertIn('З-1', text)

    def test_fresh_order_is_not_a_problem(self):
        Order.objects.create(
            number='З-2', customer_name='Иванов Иван',
            customer_phone='+79990000000', status='new')
        text = self.letter(dry_run=True)
        self.assertNotIn('ждут подтверждения', text)

    def make_inbound(self, processed=True):
        from .models import Supplier
        supplier = Supplier.objects.create(name='ООО Поставка')
        document = InboundDocument.objects.create(
            doc_number='ПР-1', doc_date=date.today(), supplier=supplier,
            warehouse=self.warehouse, created_by=self.user,
            processed=processed)
        InboundItem.objects.create(
            inbound_doc=document, material=self.material,
            quantity=Decimal('100'), unit_price=Decimal('10'))
        Stock.objects.create(warehouse=self.warehouse, material=self.material,
                             quantity=Decimal('100'))
        return document

    def test_what_happened_is_counted(self):
        self.make_inbound()
        text = self.letter(dry_run=True)
        self.assertIn('ЧТО ПРОИСХОДИЛО', text)
        self.assertIn('Приходов проведено: 1', text)

    def test_quiet_day_is_still_reported(self):
        """Молчание можно спутать со сломанной рассылкой."""
        text = self.letter(dry_run=True)
        self.assertIn('ЧТО ПРОИСХОДИЛО', text)

    def test_subject_says_whether_to_open_it(self):
        text = self.letter(dry_run=True)
        self.assertIn('Тема:', text)
        # Копий в этой проверке нет, поэтому один пункт внимания есть
        self.assertIn('Склад', text)


class SummarySendingTest(SummaryTestBase):
    """Отправка письма."""

    @override_settings(WAREHOUSE_MANAGER_EMAIL='sklad@example.ru')
    def test_letter_goes_to_the_responsible(self):
        self.letter()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['sklad@example.ru'])

    @override_settings(WAREHOUSE_MANAGER_EMAIL='один@example.ru, два@example.ru')
    def test_several_addresses(self):
        self.letter()
        self.assertEqual(mail.outbox[0].to,
                         ['один@example.ru', 'два@example.ru'])

    def test_address_can_be_given(self):
        self.letter(to='начальник@example.ru')
        self.assertEqual(mail.outbox[0].to, ['начальник@example.ru'])

    def test_dry_run_sends_nothing(self):
        self.letter(dry_run=True)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(WAREHOUSE_MANAGER_EMAIL='')
    def test_no_recipient_is_explained(self):
        with self.assertRaises(CommandError) as caught:
            self.letter()
        self.assertIn('WAREHOUSE_MANAGER_EMAIL', str(caught.exception))

    def test_zero_hours_is_refused(self):
        with self.assertRaises(CommandError):
            self.letter(hours=0)


class InstantLowStockTest(TestCase):
    """Письмо на каждое падение остатка — только по отдельной просьбе.

    По умолчанию его нет: остаток пересчитывается при каждом проведении,
    и десяток материалов даёт десяток писем в день. Ящик с такой
    рассылкой перестают читать.
    """

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='Основной', type='raw')
        self.material = Material.objects.create(
            name='Кожа хромовая', unit='m', category='leather',
            reorder_point=Decimal('50'))

    def drop_stock(self):
        Stock.objects.create(warehouse=self.warehouse, material=self.material,
                             quantity=Decimal('3'))

    def test_silent_by_default(self):
        self.drop_stock()
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(NOTIFY_LOW_STOCK_INSTANTLY=True)
    def test_can_be_switched_on(self):
        self.drop_stock()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Кожа хромовая', mail.outbox[0].subject)


class TestMailCommandTest(TestCase):
    """Проверочное письмо: им убеждаются, что почта настроена."""

    def run_it(self, **options):
        out = StringIO()
        call_command('testmail', stdout=out, stderr=out, **options)
        return out.getvalue()

    @override_settings(WAREHOUSE_MANAGER_EMAIL='sklad@example.ru')
    def test_sends_and_shows_settings(self):
        text = self.run_it()
        self.assertIn('Настройки почты', text)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['sklad@example.ru'])

    @override_settings(EMAIL_HOST_PASSWORD='очень-секретный-пароль')
    def test_password_is_not_shown(self):
        """Команду запускают при людях, и вывод попадает в переписку."""
        text = self.run_it(to='кто@example.ru')
        self.assertNotIn('очень-секретный-пароль', text)
        self.assertIn('задан', text)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend')
    def test_console_backend_is_called_out(self):
        """Про печать в консоль надо сказать прямо.

        Иначе «письмо отправлено» прочитают как «почта настроена», а
        письма никуда не уходят. В тестах Django подменяет способ
        отправки своим, поэтому здесь он задан явно.
        """
        text = self.run_it(to='кто@example.ru')
        self.assertIn('никуда не уходят', text)

    @override_settings(WAREHOUSE_MANAGER_EMAIL='')
    def test_no_recipient_is_explained(self):
        with self.assertRaises(CommandError) as caught:
            self.run_it()
        self.assertIn('WAREHOUSE_MANAGER_EMAIL', str(caught.exception))

    def test_server_answers_are_translated(self):
        """Английский ответ сервера человеку ничего не говорит."""
        from warehouse.management.commands.testmail import Command
        hint = Command.explain(Exception('535 Authentication failed'))
        self.assertIn('пароль приложения', hint)

        hint = Command.explain(Exception('Connection timed out'))
        self.assertIn('брандмауэр', hint.lower())

    def test_unknown_answer_still_points_somewhere(self):
        from warehouse.management.commands.testmail import Command
        hint = Command.explain(Exception('что-то пошло не так'))
        self.assertIn('SMTP_SETUP.md', hint)
