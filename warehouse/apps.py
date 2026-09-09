import sys

from django.apps import AppConfig


class WarehouseConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'warehouse'
    verbose_name = 'Складские операции'

    def ready(self):
        # Подключение сигналов (уведомления о низком остатке — фрагмент 19)
        from . import signals  # noqa: F401
        # Автоматическое сжатие загружаемых фото товаров
        from . import image_utils  # noqa: F401
        self._warn_about_https_on_dev_server()

    @staticmethod
    def _warn_about_https_on_dev_server():
        """Предупредить о сочетании, которое всегда заканчивается тупиком.

        Сервер разработки (runserver) говорит только по http. Если при
        этом включён USE_HTTPS, он на каждый запрос отвечает «идите на
        https» — а по https никто не отвечает. Браузер показывает
        невнятное «не может обеспечить безопасное подключение», и понять
        по нему причину нельзя.

        Сочетание бессмысленно всегда: перед сервером разработки nginx не
        ставят, ради которого USE_HTTPS и заводят. Поэтому не тихая
        запись в журнал, а заметное сообщение при запуске.
        """
        if 'runserver' not in sys.argv:
            return

        from django.conf import settings
        if not getattr(settings, 'USE_HTTPS', False):
            return

        line = '─' * 70
        sys.stderr.write(
            f'\n{line}\n'
            '  ВНИМАНИЕ: включён USE_HTTPS, а этот сервер говорит только '
            'по http.\n\n'
            '  Система будет отправлять браузер на https, где его никто '
            'не встретит,\n'
            '  и вместо страницы входа получится «сайт не может '
            'обеспечить\n'
            '  безопасное подключение» (ERR_SSL_PROTOCOL_ERROR).\n\n'
            '  Что делать: впишите в .env строку USE_HTTPS=False и '
            'запустите заново.\n'
            '  USE_HTTPS нужен, только когда впереди стоит nginx с '
            'сертификатом —\n'
            '  порядок описан в HTTPS.md.\n'
            f'{line}\n\n')
