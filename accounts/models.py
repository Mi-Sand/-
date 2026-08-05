"""
Модель пользователя системы.

Как указано в отчёте (сущность «Пользователь»), учётные записи сотрудников
хранят роль, от которой зависят права доступа: администратор, кладовщик,
экономист, менеджер.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Пользователь с ролью, определяющей права доступа."""

    class Role(models.TextChoices):
        ADMIN = 'admin', 'Администратор'
        STOREKEEPER = 'storekeeper', 'Кладовщик'
        ECONOMIST = 'economist', 'Экономист'
        MANAGER = 'manager', 'Менеджер'

    role = models.CharField(
        'Роль',
        max_length=20,
        choices=Role.choices,
        default=Role.STOREKEEPER,
    )
    patronymic = models.CharField('Отчество', max_length=150, blank=True)
    phone = models.CharField('Телефон', max_length=30, blank=True)

    class Meta:
        db_table = 'users'
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'

    def __str__(self):
        full = self.get_full_name() or self.username
        return f'{full} ({self.get_role_display()})'

    # --- Проверки прав по ролям --------------------------------------------
    @property
    def can_edit_documents(self):
        """Кладовщик и администратор могут создавать/проводить документы."""
        return self.role in (self.Role.STOREKEEPER, self.Role.ADMIN)

    @property
    def can_manage_catalog(self):
        """Ведение справочников доступно экономисту и администратору."""
        return self.role in (
            self.Role.ECONOMIST, self.Role.ADMIN, self.Role.STOREKEEPER)

    @property
    def can_process_orders(self):
        """Заказы покупателей ведут менеджер, кладовщик и администратор.

        Отгрузка сюда не входит: она списывает товар со склада и потому
        требует прав на складские документы (can_edit_documents).
        """
        return self.role in (
            self.Role.MANAGER, self.Role.STOREKEEPER, self.Role.ADMIN)

    @property
    def can_view_reports(self):
        """Отчёты доступны всем ролям, кроме отключённых учёток."""
        return self.is_active


class ChatMessage(models.Model):
    """Сообщение чата сотрудников.

    Если recipient пустой — сообщение в общий чат (видят все).
    Если recipient задан — личное сообщение между sender и recipient.
    """
    sender = models.ForeignKey(
        'User', on_delete=models.CASCADE,
        related_name='sent_messages', verbose_name='Отправитель')
    recipient = models.ForeignKey(
        'User', on_delete=models.CASCADE, null=True, blank=True,
        related_name='received_messages', verbose_name='Получатель')
    text = models.TextField('Текст')
    created_at = models.DateTimeField('Отправлено', auto_now_add=True)

    class Meta:
        db_table = 'chat_messages'
        ordering = ['created_at']
        verbose_name = 'Сообщение чата'
        verbose_name_plural = 'Сообщения чата'

    def __str__(self):
        target = self.recipient or 'общий чат'
        return f'{self.sender} → {target}: {self.text[:30]}'


class ChatReadState(models.Model):
    """Докуда сотрудник дочитал переписку.

    Хранится по одной записи на каждый диалог: отдельно для общего чата
    (peer пустой) и отдельно для переписки с каждым коллегой. Запоминается
    номер последнего прочитанного сообщения — всё, что появилось после
    него, считается непрочитанным.

    Такой способ работает одинаково и для общего чата, где сообщение
    видят все, и для личной переписки: не нужно хранить отметку
    прочтения на каждое сообщение для каждого сотрудника.
    """
    user = models.ForeignKey(
        'User', on_delete=models.CASCADE,
        related_name='chat_read_states', verbose_name='Сотрудник')
    peer = models.ForeignKey(
        'User', on_delete=models.CASCADE, null=True, blank=True,
        related_name='chat_read_by', verbose_name='Собеседник')
    last_read_id = models.PositiveIntegerField(
        'Последнее прочитанное сообщение', default=0)

    class Meta:
        db_table = 'chat_read_state'
        verbose_name = 'Отметка прочтения чата'
        verbose_name_plural = 'Отметки прочтения чата'
        constraints = [
            # Одна запись на пару «сотрудник — собеседник»
            models.UniqueConstraint(
                fields=['user', 'peer'],
                condition=models.Q(peer__isnull=False),
                name='uniq_chat_read_private'),
            # И одна запись на общий чат: в базе NULL не равен NULL,
            # поэтому для него нужно отдельное ограничение.
            models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(peer__isnull=True),
                name='uniq_chat_read_general'),
        ]

    def __str__(self):
        where = self.peer or 'общий чат'
        return f'{self.user} читал {where} до №{self.last_read_id}'
