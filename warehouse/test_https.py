"""Настройки работы по HTTPS.

Здесь две беды, и обе тихие. Включить шифрование в настройках, когда
его на деле нет, — значит закрыть себе вход: браузер уйдёт на адрес,
которого не существует, а куки с пометкой «только по защищённому
соединению» просто не дойдут. Забыть перечислить адреса — получить
«подделка запроса» на форме входа, причём не сразу.
"""
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

SETTINGS_FILE = (Path(__file__).resolve().parent.parent
                 / 'warehouse_config' / 'settings.py')


def settings_with(**environment):
    """Прочитать настройки проекта заново с другим окружением.

    Настройки читают переменные окружения один раз при запуске, поэтому
    проверить «что будет, если включить USE_HTTPS» иначе нельзя.

    Файл выполняется в чистом пространстве имён, а не перезагружается:
    обычная перезагрузка оставляет значения от прошлого раза, и
    выключенный HTTPS выглядел бы включённым — проверка молча
    подтверждала бы неправду.
    """
    namespace = {'__file__': str(SETTINGS_FILE), '__name__': 'проба_настроек'}
    with patch.dict('os.environ', environment, clear=False):
        code = SETTINGS_FILE.read_text(encoding='utf-8')
        exec(compile(code, str(SETTINGS_FILE), 'exec'), namespace)
    return SimpleNamespace(**{k: v for k, v in namespace.items()
                              if not k.startswith('__')})


class HttpsSettingsTest(TestCase):
    """Что включается вместе с USE_HTTPS."""

    def test_off_by_default(self):
        """По умолчанию ничего не включается: HTTPS ещё нет.

        Включить его в настройках раньше, чем на деле, — значит закрыть
        себе вход.
        """
        loaded = settings_with(USE_HTTPS='False')
        self.assertFalse(loaded.USE_HTTPS)
        self.assertFalse(getattr(loaded, 'SECURE_SSL_REDIRECT', False))
        self.assertFalse(getattr(loaded, 'SESSION_COOKIE_SECURE', False))

    def test_everything_turns_on_together(self):
        loaded = settings_with(USE_HTTPS='True')
        self.assertTrue(loaded.SECURE_SSL_REDIRECT)
        self.assertTrue(loaded.SESSION_COOKIE_SECURE)
        self.assertTrue(loaded.CSRF_COOKIE_SECURE)
        self.assertGreater(loaded.SECURE_HSTS_SECONDS, 0)

    def test_proxy_header_is_set(self):
        """Без него браузер ходит по кругу.

        Приложение стоит за nginx, и до него запрос доходит уже по
        обычному HTTP. О том, что снаружи было шифрование, говорит
        заголовок — иначе система считает соединение незащищённым и
        снова отправляет браузер на https.
        """
        loaded = settings_with(USE_HTTPS='True')
        self.assertEqual(loaded.SECURE_PROXY_SSL_HEADER,
                         ('HTTP_X_FORWARDED_PROTO', 'https'))

    def test_trusted_origins_are_read(self):
        loaded = settings_with(
            USE_HTTPS='True',
            CSRF_TRUSTED_ORIGINS='https://sklad.leko.local, https://192.168.1.50')
        self.assertEqual(loaded.CSRF_TRUSTED_ORIGINS,
                         ['https://sklad.leko.local', 'https://192.168.1.50'])

    def test_hsts_is_not_forever(self):
        """Память браузера о https снять до срока нельзя.

        Полгода — разумный предел: ошибка с сертификатом закроет доступ
        надолго, но не навсегда. Годы здесь были бы безрассудством.
        """
        loaded = settings_with(USE_HTTPS='True')
        self.assertLessEqual(loaded.SECURE_HSTS_SECONDS, 60 * 60 * 24 * 366)
        self.assertFalse(loaded.SECURE_HSTS_PRELOAD)

    def test_cheap_protections_are_always_on(self):
        """Эти две ничего не ломают по http, поэтому включены всегда."""
        loaded = settings_with(USE_HTTPS='False')
        self.assertTrue(loaded.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(loaded.X_FRAME_OPTIONS, 'DENY')


class HttpsCheckTest(TestCase):
    """Проверка установки должна ловить несогласованные настройки."""

    def check(self):
        """Прогнать проверку установки и вернуть её вывод.

        Команда завершает работу ненулевым кодом, когда находит
        мешающее работе, — для сценариев развёртывания это правильно, а
        здесь нам нужен только текст.
        """
        out = StringIO()
        try:
            call_command('checksetup', stdout=out, stderr=out)
        except SystemExit:
            pass
        return out.getvalue()

    @override_settings(USE_HTTPS=True, CSRF_TRUSTED_ORIGINS=[])
    def test_https_without_origins_is_an_error(self):
        text = self.check()
        self.assertIn('не заданы доверенные адреса', text)
        self.assertIn('CSRF_TRUSTED_ORIGINS', text)

    @override_settings(USE_HTTPS=True,
                       CSRF_TRUSTED_ORIGINS=['https://sklad.leko.local'])
    def test_https_with_origins_is_fine(self):
        text = self.check()
        self.assertIn('Включён режим HTTPS', text)
        self.assertNotIn('не заданы доверенные адреса', text)

    @override_settings(USE_HTTPS=True,
                       CSRF_TRUSTED_ORIGINS=['http://sklad.leko.local'])
    def test_http_origin_under_https_is_noticed(self):
        """Адрес с http при включённом шифровании просто не сработает."""
        text = self.check()
        self.assertIn('без https', text)

    @override_settings(USE_HTTPS=False, DEBUG=False)
    def test_plain_http_is_explained_not_scolded(self):
        """Внутри предприятия работа по http приемлема — это не ошибка."""
        text = self.check()
        self.assertIn('Работа по HTTP', text)
        self.assertIn('HTTPS.md', text)
