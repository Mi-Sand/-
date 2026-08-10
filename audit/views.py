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
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from warehouse.params import contains_any_case, read_date, read_id

from .models import AuditEntry
from .serializers import AuditEntrySerializer


class AuditEntryViewSet(viewsets.ReadOnlyModelViewSet):
    """Журнал с отбором по пользователю, дате, действию и виду записи."""

    queryset = AuditEntry.objects.select_related('user')
    serializer_class = AuditEntrySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        entries = super().get_queryset()
        params = self.request.query_params

        user = read_id(params.get('user'))
        if user:
            entries = entries.filter(user_id=user)

        action = params.get('action')
        if action in dict(AuditEntry.Action.choices):
            entries = entries.filter(action=action)

        model_label = params.get('model')
        if model_label:
            entries = entries.filter(model_label=model_label)

        since = read_date(params.get('since'))
        if since:
            entries = entries.filter(happened_at__date__gte=since)
        until = read_date(params.get('until'))
        if until:
            entries = entries.filter(happened_at__date__lte=until)

        search = (params.get('search') or '').strip()
        if search:
            entries = entries.filter(
                contains_any_case('object_label', search))

        return entries


@login_required
def audit_page(request):
    return render(request, 'audit.html', {'active': 'audit'})
