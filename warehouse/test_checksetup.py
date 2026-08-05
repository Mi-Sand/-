"""Проверка команды `manage.py checksetup`.

Команда нужна ровно тогда, когда установка пошла не так, — поэтому важно,
чтобы она сама не падала и находила проблемы, а не молчала о них.
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

User = get_user_model()


def run(**options):
    """Выполнить команду и вернуть (вывод, код завершения)."""
    out = StringIO()
    try:
        call_command('checksetup', stdout=out, stderr=out, **options)
    except SystemExit as exit_signal:
        return out.getvalue(), exit_signal.code
    return out.getvalue(), 0


class ChecksetupTest(TestCase):
    def setUp(self):
        User.objects.create_user('admin', password='x', is_superuser=True)

    def test_runs_without_crashing(self):
        output, _ = run()
        self.assertIn('Проверка установки', output)

    def test_reports_python_and_django(self):
        output, _ = run()
        self.assertIn('Python', output)
        self.assertIn('Django', output)

    @override_settings(DEBUG=False, SECRET_KEY='django-insecure-short')
    def test_insecure_key_is_a_problem_in_production(self):
        output, code = run(production=True)
        self.assertIn('небезопасный SECRET_KEY', output)
        self.assertEqual(code, 1, 'Небезопасный ключ должен мешать работе')

    @override_settings(DEBUG=True, SECRET_KEY='django-insecure-short')
    def test_insecure_key_is_only_a_warning_in_debug(self):
        output, code = run()
        self.assertIn('отладочный SECRET_KEY', output)
        self.assertEqual(code, 0, 'В разработке это лишь замечание')

    @override_settings(DEBUG=True)
    def test_debug_is_a_problem_only_in_production_mode(self):
        _, code = run()
        self.assertEqual(code, 0)
        output, code = run(production=True)
        self.assertIn('Включён отладочный режим', output)
        self.assertEqual(code, 1)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['*'])
    def test_wildcard_host_warned(self):
        output, _ = run()
        self.assertIn('разрешает любой адрес', output)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['sklad.leko.local'])
    def test_named_hosts_reported(self):
        output, _ = run()
        self.assertIn('sklad.leko.local', output)

    def test_missing_admin_is_a_problem(self):
        User.objects.all().delete()
        output, code = run()
        self.assertIn('Не заведено ни одного сотрудника', output)
        self.assertEqual(code, 1)

    def test_user_without_superuser_is_a_problem(self):
        User.objects.all().delete()
        User.objects.create_user('ivanov', password='x', role='storekeeper')
        output, code = run()
        self.assertIn('Нет ни одного администратора', output)
        self.assertEqual(code, 1)

    def test_missing_warehouse_is_a_warning(self):
        """Без склада не оприходовать товар, но запуску это не мешает.

        Код возврата здесь не проверяем: в тестовой среде DEBUG=False и
        статика не собрана, из-за чего команда справедливо сообщает о
        другой проблеме. Важно, что склад попал в замечания («!»), а не
        в то, что мешает работе («✗»).
        """
        output, _ = run()
        self.assertIn('! Не заведено ни одного склада', output)
        self.assertNotIn('✗ Не заведено ни одного склада', output)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend')
    def test_console_email_recognised(self):
        """Консольный backend не должен приниматься за настроенный SMTP."""
        output, _ = run()
        self.assertIn('выводятся в консоль', output)
        self.assertNotIn('не задан адрес сервера', output)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='')
    def test_smtp_without_host_warned(self):
        output, _ = run()
        self.assertIn('не задан адрес сервера', output)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='smtp.yandex.ru')
    def test_configured_smtp_reported(self):
        output, _ = run()
        self.assertIn('smtp.yandex.ru', output)

    def test_hints_are_actionable(self):
        """Отказ должен говорить, что делать, а не только что не так."""
        User.objects.all().delete()
        output, _ = run()
        self.assertIn('createsuperuser', output)

    def test_database_and_migrations_checked(self):
        output, _ = run()
        self.assertIn('База данных доступна', output)
        self.assertIn('Миграции применены', output)
