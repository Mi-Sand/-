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
