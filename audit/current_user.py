"""Кто сейчас работает — для сигналов, которым не достаётся запрос.

Сигналы `post_save` и `post_delete` знают об изменённой записи всё,
кроме самого нужного: кто её изменил. Запроса им не передают. Обычный
выход — запомнить пользователя на время обработки запроса и брать его
оттуда.

Хранилище — `ContextVar`, а не `threading.local`: при работе через
ASGI один поток обслуживает несколько запросов вперемежку, и обычная
переменная потока приписала бы действие соседнему пользователю. Ошибка
такого рода не падает, а тихо портит журнал — то есть ровно то, ради
чего журнал заводили.

Значение всегда снимается обратно в `finally`: рабочие потоки живут
долго и переиспользуются, а оставленный пользователь приписал бы себе
всё, что делают команды и задания по расписанию.
"""
from contextvars import ContextVar

_current = ContextVar('audit_current_user', default=None)


def get_current_user():
    """Пользователь текущего запроса или None (команда, планировщик)."""
    return _current.get()


def set_current_user(user):
    """Запомнить пользователя. Возвращает метку для `reset_current_user`."""
    return _current.set(user)


def reset_current_user(token):
    _current.reset(token)


class CurrentUserMiddleware:
    """Запоминает вошедшего пользователя на время обработки запроса."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and not user.is_authenticated:
            # Витрина открыта без входа: заказ покупателя оформляет
            # не сотрудник, и подписывать им журнал нечем.
            user = None
        token = set_current_user(user)
        try:
            return self.get_response(request)
        finally:
            reset_current_user(token)
