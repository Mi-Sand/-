"""Проверка отправки почты.

    python manage.py testmail
    python manage.py testmail --to ivan@example.ru

Настройка почты — это правка .env, и ошибиться в ней легко: не тот порт,
не тот пароль, не включено шифрование. Обнаруживать это в тот день,
когда понадобится предупреждение о недостаче, — поздно.

Команда показывает нынешние настройки (пароль не показывает) и
отправляет одно письмо. Если что-то не так, она говорит, что именно, а
не выводит английский текст ошибки от почтового сервера.
"""
import smtplib

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError

#: Что подсказать при частых ответах почтового сервера
HINTS = (
    ('authentication', 'Не подошли имя или пароль. Для Яндекса и Gmail '
                       'нужен не пароль от почты, а отдельный пароль '
                       'приложения — см. SMTP_SETUP.md.'),
    ('username and password', 'Не подошли имя или пароль — см. SMTP_SETUP.md.'),
    ('starttls', 'Сервер ждёт другого шифрования. Обычно помогает '
                 'EMAIL_USE_TLS=True с портом 587 либо EMAIL_USE_SSL=True '
                 'с портом 465.'),
    ('ssl', 'Не сошлось шифрование: порт 465 работает с EMAIL_USE_SSL=True, '
            'порт 587 — с EMAIL_USE_TLS=True.'),
    ('timed out', 'Сервер не ответил. Проверьте адрес и порт, а также не '
                  'закрыт ли исходящий порт брандмауэром предприятия.'),
    ('name or service not known', 'Не найден адрес почтового сервера — '
                                  'проверьте EMAIL_HOST.'),
    ('connection refused', 'Сервер отказал в соединении: скорее всего не тот '
                           'порт.'),
)


class Command(BaseCommand):
    help = 'Отправить проверочное письмо и показать настройки почты'

    def add_arguments(self, parser):
        parser.add_argument(
            '--to', default=None, metavar='АДРЕС',
            help='Кому отправить. По умолчанию — адрес ответственного из '
                 'настроек (WAREHOUSE_MANAGER_EMAIL).')

    def handle(self, *args, **options):
        backend = settings.EMAIL_BACKEND.rsplit('.', 2)[-2:]
        self.stdout.write('')
        self.stdout.write('  Настройки почты')
        self.stdout.write(f'    Способ отправки: {".".join(backend)}')
        self.stdout.write(f'    Сервер:          '
                          f'{settings.EMAIL_HOST or "не задан"}:'
                          f'{settings.EMAIL_PORT}')
        self.stdout.write(f'    Шифрование:      '
                          f'TLS={settings.EMAIL_USE_TLS}, '
                          f'SSL={settings.EMAIL_USE_SSL}')
        self.stdout.write(f'    Имя:             '
                          f'{settings.EMAIL_HOST_USER or "не задано"}')
        # Пароль не показываем даже частично: команду запускают при
        # людях, и вывод попадает в переписку с тем, кто помогает.
        self.stdout.write(f'    Пароль:          '
                          f'{"задан" if settings.EMAIL_HOST_PASSWORD else "не задан"}')
        self.stdout.write(f'    Отправитель:     {settings.DEFAULT_FROM_EMAIL}')
        self.stdout.write('')

        if 'console' in settings.EMAIL_BACKEND:
            self.stdout.write(self.style.WARNING(
                '  Письма сейчас печатаются в это окно и никуда не уходят.'))
            self.stdout.write(
                '  Так задумано для проверки на своём компьютере. Чтобы '
                'письма отправлялись\n'
                '  по-настоящему, впишите в .env настройки почты — порядок '
                'в SMTP_SETUP.md.')
            self.stdout.write('')

        recipient = options['to'] or getattr(
            settings, 'WAREHOUSE_MANAGER_EMAIL', '')
        if not recipient:
            raise CommandError(
                'Некому отправлять: не задан адрес получателя.\n'
                '    Впишите в .env WAREHOUSE_MANAGER_EMAIL или укажите '
                'адрес прямо: --to имя@предприятие.рф')

        self.stdout.write(f'  Отправляю проверочное письмо на {recipient}...')
        try:
            send_mail(
                subject='Проверка связи — складской учёт ООО «ЛЕКО»',
                message=(
                    'Это проверочное письмо от системы складского учёта.\n\n'
                    'Если вы его читаете, отправка почты настроена верно: '
                    'предупреждения\n'
                    'о низких остатках, недостачах и ежедневная сводка '
                    'будут доходить.\n\n'
                    'Отвечать на это письмо не нужно.\n'),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False)
        except (smtplib.SMTPException, OSError) as error:
            raise CommandError(self.explain(error))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('  Письмо отправлено.'))
        if 'console' not in settings.EMAIL_BACKEND:
            self.stdout.write(
                '  Проверьте ящик — и папку «Спам» тоже: письма от новых '
                'отправителей\n'
                '  часто попадают туда.')
        self.stdout.write('')

    @staticmethod
    def explain(error):
        """Перевести отказ почтового сервера в понятный совет."""
        text = str(error)
        message = f'письмо не отправлено.\n    Ответ сервера: {text}'
        lowered = text.lower()
        for marker, hint in HINTS:
            if marker in lowered:
                return f'{message}\n\n    {hint}'
        return (f'{message}\n\n    Порядок настройки — в SMTP_SETUP.md. '
                f'Проверьте EMAIL_HOST, EMAIL_PORT,\n'
                f'    EMAIL_HOST_USER и EMAIL_HOST_PASSWORD в файле .env.')
