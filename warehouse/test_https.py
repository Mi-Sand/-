"""Настройки работы по HTTPS.

Здесь две беды, и обе тихие. Включить шифрование в настройках, когда
его на деле нет, — значит закрыть себе вход: браузер уйдёт на адрес,
которого не существует, а куки с пометкой «только по защищённому
соединению» просто не дойдут. Забыть перечислить адреса — получить
«подделка запроса» на форме входа, причём не сразу.
"""
import sys
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

    @override_settings(USE_HTTPS=True,
                       CSRF_TRUSTED_ORIGINS=['https://sklad.leko.local'])
    def test_way_back_is_explained(self):
        """Подсказка про возврат нужна именно тогда, когда всё «в порядке».

        Настройки могут быть согласованы, а HTTPS всё равно не работать —
        сертификат не тот, nginx не запущен. Снаружи это выглядит как
        «не может обеспечить безопасное подключение», и по такому
        сообщению причину не угадать.
        """
        text = self.check()
        self.assertIn('ERR_SSL_PROTOCOL_ERROR', text)
        self.assertIn('USE_HTTPS=False', text)

    @override_settings(USE_HTTPS=True,
                       CSRF_TRUSTED_ORIGINS=['https://sklad.leko.local'])
    def test_csrf_symptom_is_named(self):
        """У той же беды есть второе лицо, совсем на первое не похожее.

        Когда впереди стоит nginx, он говорит системе «соединение
        защищено», даже если сотрудник открыл её по обычному http.
        Перенаправления тогда не происходит, страница входа открывается
        как ни в чём не бывало — а куки уходят с пометкой «только по
        защищённому соединению», и браузер их не сохраняет. Нажатие
        «Войти» отвечает «Ошибка проверки CSRF», и связать это с
        настройками шифрования без подсказки невозможно.
        """
        text = self.check()
        self.assertIn('CSRF', text)


class RedirectIsRecoverableTest(TestCase):
    """Перенаправление на https не должно быть необратимым.

    «301 Moved Permanently» браузеры запоминают насовсем: выключить
    USE_HTTPS обратно уже не помогает, потому что запрос до сервера
    просто не доходит. Разбирать это приходится через настройки самого
    браузера — то есть силами того, кто знает, что такое HSTS и кэш
    перенаправлений.
    """

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_redirect_is_temporary(self):
        """Главная проверка: 302, а не 301."""
        response = self.client.get('/login/')
        self.assertEqual(response.status_code, 302)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_redirect_leads_to_https(self):
        """Временное — не значит бесполезное: адрес тот же."""
        response = self.client.get('/login/')
        self.assertTrue(response['Location'].startswith('https://'))
        self.assertTrue(response['Location'].endswith('/login/'))

    @override_settings(SECURE_SSL_REDIRECT=False)
    def test_no_redirect_without_https(self):
        """Без USE_HTTPS страница входа открывается как обычно."""
        response = self.client.get('/login/')
        self.assertEqual(response.status_code, 200)

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_HSTS_SECONDS=3600)
    def test_hsts_still_works_over_https(self):
        """Постоянство даёт HSTS — от замены 301 на 302 оно не пропало."""
        response = self.client.get('/login/', secure=True)
        self.assertIn('max-age=3600',
                      response.headers.get('Strict-Transport-Security', ''))


class HstsPeriodTest(TestCase):
    """Срок памяти браузера должен настраиваться.

    Пока он был зашит в программу, у запертого снаружи администратора не
    оставалось никакого хода: снять память нельзя ни настройкой, ни
    перезапуском.
    """

    def test_default_is_half_a_year(self):
        """Кто не трогал .env, получает прежние полгода."""
        loaded = settings_with(USE_HTTPS='True')
        self.assertEqual(loaded.SECURE_HSTS_SECONDS, 60 * 60 * 24 * 180)

    def test_can_be_switched_off_while_setting_up(self):
        """Ноль — «не запоминать»: вернуться к http можно в любой миг."""
        loaded = settings_with(USE_HTTPS='True', HSTS_SECONDS='0')
        self.assertEqual(loaded.SECURE_HSTS_SECONDS, 0)

    def test_can_be_set_to_a_day(self):
        loaded = settings_with(USE_HTTPS='True', HSTS_SECONDS='86400')
        self.assertEqual(loaded.SECURE_HSTS_SECONDS, 86400)

    def test_nonsense_value_is_explained_at_start(self):
        """Опечатка не должна оборачиваться загадочной поломкой позже."""
        with self.assertRaises(RuntimeError) as caught:
            settings_with(USE_HTTPS='True', HSTS_SECONDS='полгода')
        self.assertIn('HSTS_SECONDS', str(caught.exception))


class DevServerWarningTest(TestCase):
    """Сервер разработки и USE_HTTPS вместе — всегда тупик.

    Перед runserver никто не ставит nginx, ради которого USE_HTTPS и
    заводят. Молчать про это сочетание нельзя: браузер покажет невнятное
    «не может обеспечить безопасное подключение», а в чём дело — не
    скажет никто.
    """

    def warning(self, argv, use_https):
        from warehouse.apps import WarehouseConfig

        stderr = StringIO()
        with patch.object(sys, 'argv', argv), \
                patch.object(sys, 'stderr', stderr), \
                override_settings(USE_HTTPS=use_https):
            WarehouseConfig._warn_about_https_on_dev_server()
        return stderr.getvalue()

    def test_warns_on_runserver_with_https(self):
        text = self.warning(['manage.py', 'runserver'], use_https=True)
        self.assertIn('USE_HTTPS', text)
        self.assertIn('ERR_SSL_PROTOCOL_ERROR', text)

    def test_silent_on_runserver_without_https(self):
        self.assertEqual(
            self.warning(['manage.py', 'runserver'], use_https=False), '')

    def test_silent_on_other_commands(self):
        """При обычных командах предупреждение только мешало бы."""
        self.assertEqual(
            self.warning(['manage.py', 'migrate'], use_https=True), '')
