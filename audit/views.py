"""Просмотр журнала действий.

Журнал только читают. Записи в нём не правят и не удаляют — иначе он
перестаёт быть свидетельством: подчистить за собой смог бы тот самый
человек, ради которого журнал и ведётся. Поэтому здесь ReadOnlyViewSet,
а не обычный: отсутствие обработчиков записи надёжнее проверки прав.

Читать может любой вошедший сотрудник. Это тот же принцип, что и в
остальных правах: опасны не просмотры, а изменения. Кладовщик, который
ищет причину расхождения при инвентаризации, не должен ждать
администратора.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils.dateparse import parse_date
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .models import AuditEntry
from .serializers import AuditEntrySerializer

#: Наибольшее целое, какое принимает база
MAX_ID = 2 ** 63 - 1


class AuditEntryViewSet(viewsets.ReadOnlyModelViewSet):
    """Журнал с отбором по пользователю, дате, действию и виду записи."""

    queryset = AuditEntry.objects.select_related('user')
    serializer_class = AuditEntrySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        entries = super().get_queryset()
        params = self.request.query_params

        # Номер сверяется с границей целого в базе: SQLite не принимает
        # число шире 8 байт и падает на самом запросе, а не отвечает
        # пустым списком. Такое уже случалось в отчётах.
        user = params.get('user')
        if user and user.isdigit() and int(user) <= MAX_ID:
            entries = entries.filter(user_id=int(user))

        action = params.get('action')
        if action in dict(AuditEntry.Action.choices):
            entries = entries.filter(action=action)

        model_label = params.get('model')
        if model_label:
            entries = entries.filter(model_label=model_label)

        # Даты приходят от календаря в браузере, но дойти сюда может что
        # угодно. Негодную дату молча пропускаем: пустой отбор понятнее
        # ошибки на всю страницу.
        since = parse_date(params.get('since') or '')
        if since:
            entries = entries.filter(happened_at__date__gte=since)
        until = parse_date(params.get('until') or '')
        if until:
            entries = entries.filter(happened_at__date__lte=until)

        search = (params.get('search') or '').strip()
        if search:
            entries = entries.filter(object_label__icontains=search)

        return entries


@login_required
def audit_page(request):
    return render(request, 'audit.html', {'active': 'audit'})
