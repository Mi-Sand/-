"""
API управления сотрудниками (пользователями системы).

Доступ к списку и созданию/изменению сотрудников имеет только
администратор (роль admin или суперпользователь). Остальные роли получают
отказ — управление учётными записями не входит в их полномочия.
"""
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .models import User
from .serializers import UserSerializer


class IsAdminRole(IsAuthenticated):
    """Разрешает доступ только администратору или суперпользователю."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        user = request.user
        return user.is_superuser or getattr(user, 'role', None) == 'admin'


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by('last_name', 'username')
    serializer_class = UserSerializer
    permission_classes = [IsAdminRole]


from django.db.models import Max, Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status

from .models import ChatMessage
from .serializers import ChatMessageSerializer

# Отличаем «параметр не задан» от «задан, но испорчен»: для первого случая
# подходит None, для второго нужен отдельный признак.
_INVALID = object()


def _as_id(value):
    """Привести параметр запроса к целому id.

    Возвращает None, если параметр не задан, и _INVALID, если задан
    чем-то, что идентификатором быть не может. Без этой проверки строка
    вроде ?with=abc уходит прямо в запрос к базе и роняет обработчик.
    """
    if value in (None, '', 'null'):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return _INVALID
    return number if number > 0 else _INVALID


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def chat_messages(request):
    """Общий чат или личная переписка.

    GET  ?with=<user_id>  — личные сообщения с этим пользователем;
         без параметра     — общий чат (recipient пустой).
         ?after=<id>       — только сообщения новее указанного (для опроса).
    POST {text, recipient?} — отправить сообщение (recipient пустой = общий).
    """
    me = request.user

    if request.method == 'POST':
        text = (request.data.get('text') or '').strip()
        if not text:
            return Response({'error': 'Пустое сообщение'},
                            status=status.HTTP_400_BAD_REQUEST)
        recipient_id = _as_id(request.data.get('recipient'))
        if recipient_id is _INVALID:
            return Response({'error': 'Неверный получатель'},
                            status=status.HTTP_400_BAD_REQUEST)
        # Получателя проверяем по базе: иначе сообщение сохранится со
        # ссылкой на несуществующего сотрудника, и запрос закончится
        # ошибкой сервера на PostgreSQL с его немедленной проверкой связей.
        if recipient_id is not None and not User.objects.filter(
                pk=recipient_id, is_active=True).exists():
            return Response({'error': 'Получатель не найден'},
                            status=status.HTTP_400_BAD_REQUEST)
        msg = ChatMessage.objects.create(
            sender=me, recipient_id=recipient_id, text=text)
        return Response(ChatMessageSerializer(msg).data,
                        status=status.HTTP_201_CREATED)

    # GET
    with_user = _as_id(request.query_params.get('with'))
    after = _as_id(request.query_params.get('after'))
    if with_user is _INVALID or after is _INVALID:
        return Response({'error': 'Неверный параметр запроса'},
                        status=status.HTTP_400_BAD_REQUEST)

    if with_user:
        # Личная переписка между me и with_user (в обе стороны)
        qs = ChatMessage.objects.filter(
            (Q(sender=me) & Q(recipient_id=with_user)) |
            (Q(sender_id=with_user) & Q(recipient=me)))
    else:
        # Общий чат — сообщения без получателя
        qs = ChatMessage.objects.filter(recipient__isnull=True)

    if after:
        qs = qs.filter(id__gt=after)

    qs = qs.order_by('created_at')[:200]
    return Response(ChatMessageSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def chat_contacts(request):
    """Список сотрудников для личных чатов (все, кроме себя)."""
    users = User.objects.exclude(id=request.user.id).filter(
        is_active=True).order_by('last_name', 'username')
    data = [{
        'id': u.id,
        'name': ' '.join(p for p in [u.last_name, u.first_name] if p) or u.username,
        'role': u.get_role_display(),
    } for u in users]
    return Response(data)


from .models import ChatReadState


def _read_state(user, peer_id=None):
    """Найти или создать отметку прочтения для диалога."""
    state, _ = ChatReadState.objects.get_or_create(
        user=user, peer_id=peer_id, defaults={'last_read_id': 0})
    return state


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def chat_unread(request):
    """Сколько непрочитанных сообщений и где именно.

    Возвращает:
        {
          "total": 5,                  — всего непрочитанных
          "general": 2,                — в общем чате
          "private": {"3": 3},         — по собеседникам (id: количество)
          "latest": {                  — последнее пришедшее сообщение,
            "id": 42,                  -  чтобы показать уведомление
            "sender_id": 3,
            "sender_name": "Анна Петрова",
            "text": "Привет",
            "is_private": true
          }
        }
    """
    me = request.user

    # Отметки прочтения по всем диалогам сразу — одним запросом
    states = {s.peer_id: s.last_read_id
              for s in ChatReadState.objects.filter(user=me)}

    # Общий чат: чужие сообщения новее отметки
    general_qs = (ChatMessage.objects
                  .filter(recipient__isnull=True)
                  .exclude(sender=me)
                  .filter(id__gt=states.get(None, 0)))
    general = general_qs.count()

    # Личные: непрочитанные, сгруппированные по отправителю
    private = {}
    private_qs = ChatMessage.objects.filter(recipient=me)
    for row in private_qs.values('sender_id').annotate(last=Max('id')):
        sender_id = row['sender_id']
        unread = (private_qs
                  .filter(sender_id=sender_id,
                          id__gt=states.get(sender_id, 0))
                  .count())
        if unread:
            private[str(sender_id)] = unread

    # Последнее непрочитанное — для всплывающего уведомления
    latest = None
    candidates = list(general_qs.order_by('-id')[:1])
    for sender_id in private:
        msg = (private_qs
               .filter(sender_id=int(sender_id),
                       id__gt=states.get(int(sender_id), 0))
               .order_by('-id').first())
        if msg:
            candidates.append(msg)
    if candidates:
        newest = max(candidates, key=lambda m: m.id)
        parts = [newest.sender.last_name, newest.sender.first_name]
        name = ' '.join(p for p in parts if p) or newest.sender.username
        latest = {
            'id': newest.id,
            'sender_id': newest.sender_id,
            'sender_name': name,
            'text': newest.text[:120],
            'is_private': newest.recipient_id is not None,
        }

    return Response({
        'total': general + sum(private.values()),
        'general': general,
        'private': private,
        'latest': latest,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def chat_mark_read(request):
    """Отметить диалог прочитанным.

    Принимает {"with": <id собеседника>} для личной переписки
    или пустое тело для общего чата.
    """
    me = request.user
    peer_id = _as_id(request.data.get('with'))
    if peer_id is _INVALID:
        return Response({'error': 'Неверный собеседник'},
                        status=status.HTTP_400_BAD_REQUEST)

    if peer_id:
        if not User.objects.filter(pk=peer_id).exists():
            return Response({'error': 'Собеседник не найден'},
                            status=status.HTTP_400_BAD_REQUEST)
        newest = (ChatMessage.objects
                  .filter(sender_id=peer_id, recipient=me)
                  .order_by('-id').first())
    else:
        newest = (ChatMessage.objects
                  .filter(recipient__isnull=True)
                  .exclude(sender=me)
                  .order_by('-id').first())

    state = _read_state(me, peer_id)
    if newest and newest.id > state.last_read_id:
        state.last_read_id = newest.id
        state.save(update_fields=['last_read_id'])

    return Response({'status': 'ok', 'last_read_id': state.last_read_id})
