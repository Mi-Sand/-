"""Сериализаторы данных бухгалтерии."""
from rest_framework import serializers

from .models import Counterparty, Payment


class CounterpartySerializer(serializers.ModelSerializer):
    orders_count = serializers.SerializerMethodField()

    class Meta:
        model = Counterparty
        fields = ['id', 'short_name', 'inn', 'kpp', 'legal_address',
                  'phone', 'email', 'orders_count', 'created_at']
        read_only_fields = ['created_at']

    def get_orders_count(self, obj):
        return obj.orders.count()

    def validate(self, data):
        """Не заводить вторую карточку тому же покупателю.

        Ограничение уникальности в базе есть, но оно вернуло бы ошибку
        сервера. Здесь она превращается в понятное сообщение с указанием,
        какая карточка уже заведена.
        """
        inn = data.get('inn', getattr(self.instance, 'inn', None))
        kpp = data.get('kpp', getattr(self.instance, 'kpp', ''))

        existing = Counterparty.objects.filter(inn=inn, kpp=kpp or '')
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)
        found = existing.first()
        if found:
            raise serializers.ValidationError(
                f'Покупатель с такими ИНН и КПП уже заведён: '
                f'«{found.short_name}»')
        return data


class PaymentSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(
        source='order.number', read_only=True)
    customer = serializers.CharField(
        source='order.customer_name', read_only=True)
    method_display = serializers.CharField(
        source='get_method_display', read_only=True)

    class Meta:
        model = Payment
        fields = ['id', 'order', 'order_number', 'customer', 'amount',
                  'paid_at', 'method', 'method_display', 'document_number',
                  'comment', 'created_at']
        read_only_fields = ['created_at']

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                'Сумма оплаты должна быть больше нуля')
        return value

    def validate(self, data):
        """Не принимать оплату по отменённому заказу.

        Деньги за отменённый заказ — это возврат, а не оплата, и в акте
        сверки такая запись исказила бы расчёты.
        """
        order = data.get('order', getattr(self.instance, 'order', None))
        if order and order.status == 'cancelled':
            raise serializers.ValidationError(
                f'Заказ {order.number} отменён — принять оплату по нему '
                f'нельзя.')
        return data
