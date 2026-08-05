"""
Сериализатор сотрудников (пользователей системы).

Пароль обрабатывается безопасно: он не возвращается в ответах API, а при
создании/изменении хешируется через set_password. Так пароли никогда не
хранятся и не передаются в открытом виде.
"""
from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(
        source='get_role_display', read_only=True)
    # Пароль только для записи: принимается на вход, но не отдаётся в ответах.
    password = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
        style={'input_type': 'password'})
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'password', 'first_name', 'last_name',
                  'patronymic', 'phone', 'role', 'role_display',
                  'is_active', 'full_name']

    def get_full_name(self, obj):
        parts = [obj.last_name, obj.first_name, obj.patronymic]
        name = ' '.join(p for p in parts if p)
        return name or obj.username

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        # Пароль меняем только если передан непустой
        if password:
            instance.set_password(password)
        instance.save()
        return instance


from .models import ChatMessage


class ChatMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    sender_id = serializers.IntegerField(source='sender.id', read_only=True)

    class Meta:
        model = ChatMessage
        fields = ['id', 'sender_id', 'sender_name', 'recipient',
                  'text', 'created_at']
        read_only_fields = ['sender_id', 'sender_name', 'created_at']

    def get_sender_name(self, obj):
        parts = [obj.sender.last_name, obj.sender.first_name]
        name = ' '.join(p for p in parts if p)
        return name or obj.sender.username
