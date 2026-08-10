from rest_framework import serializers

from .models import AuditEntry


class AuditEntrySerializer(serializers.ModelSerializer):
    """Запись журнала для страницы просмотра.

    Имя пользователя берётся из записи, а не из связанной учётки:
    журнал должен показывать, кто действовал тогда, даже если сотрудника
    с тех пор переименовали или удалили.
    """

    action_display = serializers.CharField(
        source='get_action_display', read_only=True)

    class Meta:
        model = AuditEntry
        fields = ['id', 'happened_at', 'user', 'user_label', 'action',
                  'action_display', 'model_label', 'model_title',
                  'object_id', 'object_label', 'changes']
        read_only_fields = fields
