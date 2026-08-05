"""
Права текущего сотрудника — для шаблонов.

Ограничения проверяются на сервере, и обойти их через интерфейс нельзя.
Но показывать кнопку, которая заведомо ответит отказом, — плохо: человек
нажимает, получает «нет прав» и не понимает, зачем ему это предлагали.
Поэтому те же признаки роли передаются в страницы, и лишние кнопки просто
не отрисовываются.

Это украшение, а не защита. Скрытая кнопка не мешает отправить запрос
руками — от этого защищают классы прав в accounts/permissions.py.
"""


def user_permissions(request):
    """Добавить в контекст шаблона права текущего пользователя."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {'user_perms': {}}

    return {
        'user_perms': {
            'catalog': bool(user.is_superuser or user.can_manage_catalog),
            'documents': bool(user.is_superuser or user.can_edit_documents),
            'orders': bool(user.is_superuser or user.can_process_orders),
            'reports': bool(user.can_view_reports),
            'admin': bool(user.is_superuser or user.role == 'admin'),
            'role_display': user.get_role_display(),
        }
    }
