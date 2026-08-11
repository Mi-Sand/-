"""
Данные, нужные для печати документов бухгалтерии.

Складской учёт сам по себе о деньгах знает мало: он ведёт остатки и
движение товара. Чтобы выписать счёт или накладную, нужны сведения,
которых в складских таблицах нет:

* реквизиты своей организации — ИНН, КПП, банк, расчётный счёт, кто
  подписывает документы;
* реквизиты покупателя, если он организация, а не частное лицо;
* поступившие оплаты — без них нельзя составить акт сверки.

Всё перечисленное собрано здесь.
"""
from decimal import Decimal

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

# ИНН бывает десятизначным у организаций и двенадцатизначным у
# предпринимателей. Проверяем только длину и то, что это цифры: полная
# проверка по контрольной сумме здесь избыточна, реквизиты вводит
# бухгалтер и сверяет с договором.
inn_validator = RegexValidator(
    r'^\d{10}$|^\d{12}$',
    'ИНН состоит из 10 цифр у организации или 12 у предпринимателя')

kpp_validator = RegexValidator(
    r'^\d{9}$', 'КПП состоит из 9 цифр')

account_validator = RegexValidator(
    r'^\d{20}$', 'Номер счёта состоит из 20 цифр')

bik_validator = RegexValidator(
    r'^\d{9}$', 'БИК состоит из 9 цифр')


class CompanyRequisites(models.Model):
    """Реквизиты своей организации — продавца.

    Запись предполагается одна: это сведения о предприятии, а не
    справочник. Ограничение на единственность задано явно, чтобы вторая
    запись не появилась случайно и документы не начали печататься с
    разными реквизитами в зависимости от того, какая нашлась первой.
    """

    VAT_CHOICES = [
        ('20', 'НДС 20%'),
        ('10', 'НДС 10%'),
        ('0', 'НДС 0%'),
        ('none', 'Без НДС (УСН)'),
    ]

    is_active = models.BooleanField(
        'Действующие реквизиты', default=True, editable=False)

    short_name = models.CharField(
        'Краткое наименование', max_length=255,
        help_text='Например: ООО «ЛЕКО»')
    full_name = models.CharField(
        'Полное наименование', max_length=500,
        help_text='Как в уставе: Общество с ограниченной ответственностью…')
    inn = models.CharField('ИНН', max_length=12, validators=[inn_validator])
    kpp = models.CharField(
        'КПП', max_length=9, blank=True, validators=[kpp_validator],
        help_text='У индивидуальных предпринимателей КПП нет — оставьте пустым')
    ogrn = models.CharField('ОГРН', max_length=15, blank=True)

    legal_address = models.CharField('Юридический адрес', max_length=500)
    postal_address = models.CharField(
        'Почтовый адрес', max_length=500, blank=True,
        help_text='Заполните, если отличается от юридического')
    phone = models.CharField('Телефон', max_length=50, blank=True)

    # --- Банк ---------------------------------------------------------------
    bank_name = models.CharField('Банк', max_length=255)
    bank_bik = models.CharField(
        'БИК', max_length=9, validators=[bik_validator])
    settlement_account = models.CharField(
        'Расчётный счёт', max_length=20, validators=[account_validator])
    correspondent_account = models.CharField(
        'Корреспондентский счёт', max_length=20, blank=True,
        validators=[account_validator])

    # --- Подписанты ---------------------------------------------------------
    director_position = models.CharField(
        'Должность руководителя', max_length=100, default='Директор')
    director_name = models.CharField(
        'Руководитель', max_length=255,
        help_text='Как в подписи: Иванов И. И.')
    accountant_name = models.CharField(
        'Главный бухгалтер', max_length=255, blank=True,
        help_text='Оставьте пустым, если обязанности ведёт руководитель')

    # --- Налог --------------------------------------------------------------
    vat_rate = models.CharField(
        'Ставка НДС', max_length=10, choices=VAT_CHOICES, default='20')
    vat_included_in_price = models.BooleanField(
        'Цены указаны с НДС', default=True,
        help_text='В рознице цена обычно уже включает налог. Если снять '
                  'галочку, НДС будет начисляться сверх цены товара.')

    updated_at = models.DateTimeField('Изменены', auto_now=True)

    class Meta:
        db_table = 'company_requisites'
        verbose_name = 'Реквизиты организации'
        verbose_name_plural = 'Реквизиты организации'
        constraints = [
            models.UniqueConstraint(
                fields=['is_active'],
                condition=models.Q(is_active=True),
                name='uniq_active_company_requisites'),
        ]

    def __str__(self):
        return self.short_name

    @property
    def vat_percent(self):
        """Ставка налога числом. Для режима без НДС — None."""
        if self.vat_rate == 'none':
            return None
        return Decimal(self.vat_rate)

    @property
    def vat_display(self):
        """Как ставка печатается в бланке."""
        if self.vat_rate == 'none':
            return 'Без налога (НДС)'
        return f'{self.vat_rate}%'

    @classmethod
    def get_active(cls):
        """Действующие реквизиты либо None, если их ещё не заполнили."""
        return cls.objects.filter(is_active=True).first()


class Counterparty(models.Model):
    """Реквизиты покупателя-организации.

    Заказы приходят с витрины от частных лиц: там есть имя, телефон и
    адрес доставки, но нет ИНН и КПП. Выписать таким покупателям счёт
    можно и без реквизитов, а вот накладную или УПД организации — уже
    нет. Поэтому реквизиты хранятся отдельно и привязываются к заказу,
    когда покупатель оказался юридическим лицом.
    """

    short_name = models.CharField('Наименование', max_length=255,
                                  db_index=True)
    inn = models.CharField('ИНН', max_length=12, validators=[inn_validator],
                           db_index=True)
    kpp = models.CharField('КПП', max_length=9, blank=True,
                           validators=[kpp_validator])
    legal_address = models.CharField('Адрес', max_length=500, blank=True)
    phone = models.CharField('Телефон', max_length=50, blank=True)
    email = models.EmailField('E-mail', blank=True)

    created_at = models.DateTimeField('Добавлен', auto_now_add=True)

    class Meta:
        db_table = 'counterparties'
        ordering = ['short_name']
        verbose_name = 'Покупатель-организация'
        verbose_name_plural = 'Покупатели-организации'
        constraints = [
            # Одна организация — одна карточка. У обособленных
            # подразделений ИНН общий, а КПП разный, поэтому в ключ
            # входят оба поля.
            models.UniqueConstraint(
                fields=['inn', 'kpp'], name='uniq_counterparty_inn_kpp'),
        ]

    def __str__(self):
        return f'{self.short_name} (ИНН {self.inn})'


class Payment(models.Model):
    """Поступившая оплата по заказу.

    Нужна для акта сверки: без сведений о платежах он показывал бы
    только отгрузки и вечно растущий долг покупателя.

    Оплата привязана к заказу, а не к контрагенту напрямую: в рознице
    платёж почти всегда закрывает конкретный заказ, а сводка по
    контрагенту собирается через заказы.
    """

    METHOD_CHOICES = [
        ('bank', 'Банковский перевод'),
        ('cash', 'Наличные'),
        ('card', 'Банковская карта'),
    ]

    order = models.ForeignKey(
        'warehouse.Order', on_delete=models.PROTECT,
        related_name='payments', verbose_name='Заказ')
    amount = models.DecimalField(
        'Сумма', max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))])
    paid_at = models.DateField('Дата оплаты', db_index=True)
    method = models.CharField(
        'Способ', max_length=20, choices=METHOD_CHOICES, default='bank')
    document_number = models.CharField(
        'Номер платёжного документа', max_length=50, blank=True,
        help_text='Номер платёжного поручения или чека')
    comment = models.CharField('Примечание', max_length=255, blank=True)

    created_at = models.DateTimeField('Внесена', auto_now_add=True)
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='Внёс')

    class Meta:
        db_table = 'payments'
        ordering = ['-paid_at', '-id']
        verbose_name = 'Оплата'
        verbose_name_plural = 'Оплаты'

    def __str__(self):
        return f'{self.amount} ₽ по заказу {self.order.number}'
