"""
Постраничная выдача списков API.

Отдельный класс нужен из-за особенности DRF: верхняя граница параметра
?limit= задаётся только атрибутом класса `max_limit`. Ключ MAX_LIMIT в
словаре REST_FRAMEWORK библиотека не читает — такой настройки у неё нет,
и запись в settings ни на что не влияла. Без класса запрос ?limit=1000000
выгружал бы всю таблицу одним ответом.
"""
from rest_framework.pagination import LimitOffsetPagination


class WarehouseLimitOffsetPagination(LimitOffsetPagination):
    """LimitOffsetPagination с работающим ограничением размера страницы."""

    # Клиентская функция apiCallAll забирает страницы подряд по ссылке
    # next, поэтому потолок не мешает выгрузить всё — только не разом.
    max_limit = 500
