"""
Модели данных системы складского учёта.

Соответствуют ER-диаграмме из отчёта. Выделены четыре группы таблиц:
справочники (материалы, продукция, склады, поставщики), документы операций
(приход, расход), строки документов и служебные таблицы (остатки, журнал
движения, история цен). База приведена к третьей нормальной форме.
"""
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


# ===========================================================================
#  СПРАВОЧНИКИ
# ===========================================================================
class Material(models.Model):
    """Сырьё и комплектующие, используемые в производстве (фрагмент 4)."""

    UNIT_CHOICES = [
        ('pc', 'шт.'), ('kg', 'кг'),
        ('m', 'м'), ('m2', 'кв. м'), ('l', 'л'),
    ]
    CATEGORY_CHOICES = [
        ('textile', 'Ткани'), ('leather', 'Кожа'),
        ('polymer', 'Полимеры'), ('fittings', 'Фурнитура'),
    ]

    name = models.CharField('Наименование', max_length=255, db_index=True)
    article_number = models.CharField('Артикул', max_length=50, blank=True)
    color = models.CharField('Цвет', max_length=50, blank=True)
    description = models.TextField('Описание', blank=True)
    unit = models.CharField('Ед. изм.', max_length=10, choices=UNIT_CHOICES)
    category = models.CharField(
        'Категория', max_length=50,
        choices=CATEGORY_CHOICES, db_index=True)
    reorder_point = models.DecimalField(
        'Минимальный остаток', max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0)])

    class Meta:
        db_table = 'materials'
        ordering = ['name']
        verbose_name = 'Материал'
        verbose_name_plural = 'Материалы'

    def __str__(self):
        return self.name


class Product(models.Model):
    """Готовая продукция — учёт по размеру и цвету (фрагмент 5)."""

    CATEGORY_CHOICES = [
        ('shoes', 'Обувь'), ('clothing', 'Одежда'),
        ('accessories', 'Аксессуары'), ('equipment', 'Инвентарь'),
    ]
    STATUS_CHOICES = [
        ('active', 'Выпускается'), ('archived', 'Снят с производства'),
    ]

    article_number = models.CharField('Артикул', max_length=50, unique=True)
    name = models.CharField('Наименование', max_length=255, db_index=True)
    category = models.CharField(
        'Категория', max_length=50, choices=CATEGORY_CHOICES, db_index=True)
    size = models.CharField('Размер', max_length=10)
    color = models.CharField('Цвет', max_length=50)
    cost = models.DecimalField(
        'Себестоимость', max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0)])
    selling_price = models.DecimalField(
        'Отпускная цена', max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0)])
    status = models.CharField(
        'Статус', max_length=20, choices=STATUS_CHOICES, default='active')
    description = models.TextField('Описание', blank=True)
    photo = models.FileField(
        'Фото', upload_to='products/photos/', blank=True, null=True)
    video = models.FileField(
        'Видео', upload_to='products/videos/', blank=True, null=True)

    class Meta:
        db_table = 'products'
        ordering = ['name', 'size']
        verbose_name = 'Готовая продукция'
        verbose_name_plural = 'Готовая продукция'

    def __str__(self):
        return f'{self.article_number} {self.name} ({self.size})'


class Warehouse(models.Model):
    """Складское помещение предприятия."""

    TYPE_CHOICES = [
        ('raw', 'Склад сырья'),
        ('finished', 'Склад готовой продукции'),
    ]

    name = models.CharField('Наименование', max_length=255)
    type = models.CharField('Тип', max_length=20, choices=TYPE_CHOICES)
    location = models.CharField('Расположение', max_length=255, blank=True)
    capacity = models.PositiveIntegerField('Вместимость', null=True,
                                            blank=True)

    class Meta:
        db_table = 'warehouses'
        ordering = ['name']
        verbose_name = 'Склад'
        verbose_name_plural = 'Склады'

    def __str__(self):
        return self.name


class Supplier(models.Model):
    """Поставщик товаров."""

    name = models.CharField('Наименование', max_length=255, db_index=True)
    inn = models.CharField('ИНН', max_length=12, blank=True)
    contact_person = models.CharField('Контактное лицо', max_length=255,
                                      blank=True)
    phone = models.CharField('Телефон', max_length=30, blank=True)
    email = models.EmailField('E-mail', blank=True)

    class Meta:
        db_table = 'suppliers'
        ordering = ['name']
        verbose_name = 'Поставщик'
        verbose_name_plural = 'Поставщики'

    def __str__(self):
        return self.name


# ===========================================================================
#  ДОКУМЕНТЫ ОПЕРАЦИЙ И ИХ СТРОКИ
# ===========================================================================
class InboundDocument(models.Model):
    """Приходный документ — поступление товаров (фрагмент 6)."""

    doc_number = models.CharField('Номер', max_length=50, unique=True)
    doc_date = models.DateField('Дата')
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, verbose_name='Поставщик')
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, verbose_name='Склад')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, verbose_name='Создал')
    processed = models.BooleanField('Проведён', default=False)
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        db_table = 'inbound_documents'
        ordering = ['-doc_date', '-id']
        verbose_name = 'Приходный документ'
        verbose_name_plural = 'Приходные документы'

    def __str__(self):
        return f'Приход № {self.doc_number} от {self.doc_date}'

    @property
    def total_sum(self):
        return sum((i.quantity * i.unit_price for i in self.items.all()),
                   start=0)


class InboundItem(models.Model):
    """Строка приходного документа (фрагмент 6)."""

    inbound_doc = models.ForeignKey(
        InboundDocument, on_delete=models.CASCADE, related_name='items')
    material = models.ForeignKey(
        Material, null=True, blank=True, on_delete=models.PROTECT)
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.DecimalField(
        'Количество', max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0)])
    unit_price = models.DecimalField(
        'Цена за единицу', max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0)])
    batch_number = models.CharField('Партия', max_length=50, blank=True)
    expiry_date = models.DateField('Срок годности', null=True, blank=True)

    class Meta:
        db_table = 'inbound_items'
        verbose_name = 'Строка прихода'
        verbose_name_plural = 'Строки прихода'
        constraints = [
            # Строка ссылается либо на материал, либо на продукцию, но не оба
            models.CheckConstraint(
                check=(
                    models.Q(material__isnull=False, product__isnull=True) |
                    models.Q(material__isnull=True, product__isnull=False)
                ),
                name='inbound_item_material_xor_product',
            ),
        ]

    def __str__(self):
        return f'{self.item_name} × {self.quantity}'

    @property
    def item_name(self):
        return str(self.material or self.product or '—')


class OutboundDocument(models.Model):
    """Расходный документ — отпуск товаров (фрагмент 9)."""

    PURPOSE_CHOICES = [
        ('production', 'В производство'),
        ('sale', 'На продажу'),
        ('writeoff', 'Списание'),
    ]

    doc_number = models.CharField('Номер', max_length=50, unique=True)
    doc_date = models.DateField('Дата')
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, verbose_name='Склад')
    purpose = models.CharField(
        'Назначение', max_length=20, choices=PURPOSE_CHOICES)
    production_order = models.CharField(
        'Производственный заказ', max_length=50, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, verbose_name='Создал')
    processed = models.BooleanField('Проведён', default=False)
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        db_table = 'outbound_documents'
        ordering = ['-doc_date', '-id']
        verbose_name = 'Расходный документ'
        verbose_name_plural = 'Расходные документы'

    def __str__(self):
        return f'Расход № {self.doc_number} от {self.doc_date}'

    @property
    def total_sum(self):
        return sum((i.quantity * i.unit_price for i in self.items.all()),
                   start=0)


class OutboundItem(models.Model):
    """Строка расходного документа."""

    outbound_doc = models.ForeignKey(
        OutboundDocument, on_delete=models.CASCADE, related_name='items')
    material = models.ForeignKey(
        Material, null=True, blank=True, on_delete=models.PROTECT)
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.DecimalField(
        'Количество', max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0)])
    unit_price = models.DecimalField(
        'Цена за единицу', max_digits=10, decimal_places=2,
        default=0, validators=[MinValueValidator(0)])

    class Meta:
        db_table = 'outbound_items'
        verbose_name = 'Строка расхода'
        verbose_name_plural = 'Строки расхода'
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(material__isnull=False, product__isnull=True) |
                    models.Q(material__isnull=True, product__isnull=False)
                ),
                name='outbound_item_material_xor_product',
            ),
        ]

    def __str__(self):
        return f'{self.item_name} × {self.quantity}'

    @property
    def item_name(self):
        return str(self.material or self.product or '—')


# ===========================================================================
#  СЛУЖЕБНЫЕ ТАБЛИЦЫ
# ===========================================================================
class Stock(models.Model):
    """Текущий остаток товара на складе (фрагмент 7)."""

    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE, related_name='stocks')
    material = models.ForeignKey(
        Material, null=True, blank=True,
        on_delete=models.CASCADE, related_name='stock')
    product = models.ForeignKey(
        Product, null=True, blank=True,
        on_delete=models.CASCADE, related_name='stock')
    quantity = models.DecimalField(
        'Остаток', max_digits=12, decimal_places=2, default=0)
    last_updated = models.DateTimeField('Обновлён', auto_now=True)

    class Meta:
        db_table = 'stock'
        verbose_name = 'Остаток на складе'
        verbose_name_plural = 'Остатки на складах'
        constraints = [
            models.UniqueConstraint(
                fields=['warehouse', 'material', 'product'],
                name='uniq_stock_position'),
        ]

    def __str__(self):
        return f'{self.item_name}: {self.quantity} @ {self.warehouse}'

    @property
    def item_name(self):
        return str(self.material or self.product or '—')


class StockMovement(models.Model):
    """Журнал движения товаров (приход/расход/корректировка)."""

    MOVEMENT_CHOICES = [
        ('in', 'Приход'),
        ('out', 'Расход'),
        ('adjust', 'Корректировка'),
    ]

    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE)
    material = models.ForeignKey(
        Material, null=True, blank=True, on_delete=models.CASCADE)
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.CASCADE)
    movement_type = models.CharField(
        'Тип', max_length=10, choices=MOVEMENT_CHOICES)
    quantity = models.DecimalField(
        'Количество', max_digits=12, decimal_places=2)
    document_id = models.IntegerField('ID документа', null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True)
    created_at = models.DateTimeField('Дата и время', auto_now_add=True,
                                      db_index=True)

    class Meta:
        db_table = 'stock_movements'
        ordering = ['-created_at']
        verbose_name = 'Движение товара'
        verbose_name_plural = 'Журнал движения'

    def __str__(self):
        sign = '+' if self.movement_type == 'in' else '−'
        return f'{self.get_movement_type_display()} {sign}{self.quantity}'

    @property
    def item_name(self):
        return str(self.material or self.product or '—')


class PriceHistory(models.Model):
    """История закупочных цен (доработка по замечаниям, п. 2.4.3)."""

    material = models.ForeignKey(
        Material, null=True, blank=True, on_delete=models.CASCADE,
        related_name='price_history')
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.CASCADE,
        related_name='price_history')
    price = models.DecimalField('Цена', max_digits=10, decimal_places=2)
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.SET_NULL)
    recorded_at = models.DateTimeField('Зафиксирована', auto_now_add=True)

    class Meta:
        db_table = 'price_history'
        ordering = ['-recorded_at']
        verbose_name = 'История цены'
        verbose_name_plural = 'История закупочных цен'

    def __str__(self):
        item = self.material or self.product or '—'
        return f'{item}: {self.price} ({self.recorded_at:%d.%m.%Y})'


class Order(models.Model):
    """Заказ покупателя из интернет-магазина.

    Товар не списывается со склада сразу при оформлении, а резервируется:
    физически он ещё на складе, но обещан покупателю и недоступен другим.
    Реальное списание происходит при отгрузке — тогда система создаёт
    расходный документ и проводит его штатной логикой (см. services).

    Резерв не хранится отдельным полем: он вычисляется как сумма позиций
    заказов в статусах «новый» и «подтверждён». Так данные не могут
    рассинхронизироваться — резерв всегда соответствует реальным заказам.
    """

    STATUS_CHOICES = [
        ('new', 'Новый'),
        ('confirmed', 'Подтверждён'),
        ('shipped', 'Отгружен'),
        ('cancelled', 'Отменён'),
    ]

    # Статусы, при которых товар считается зарезервированным
    ACTIVE_STATUSES = ('new', 'confirmed')

    number = models.CharField('Номер', max_length=20, unique=True)
    customer_name = models.CharField('Покупатель', max_length=255)
    customer_phone = models.CharField('Телефон', max_length=30)
    customer_email = models.EmailField('E-mail', blank=True)
    address = models.CharField('Адрес доставки', max_length=500, blank=True)
    comment = models.TextField('Комментарий', blank=True)
    status = models.CharField(
        'Статус', max_length=20, choices=STATUS_CHOICES, default='new',
        db_index=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    # Документ, которым заказ был отгружен (заполняется при отгрузке)
    outbound_document = models.ForeignKey(
        'OutboundDocument', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='orders', verbose_name='Расходный документ')

    class Meta:
        db_table = 'orders'
        ordering = ['-created_at']
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'

    def __str__(self):
        return f'Заказ {self.number} — {self.customer_name}'

    @property
    def total(self):
        """Сумма заказа по ценам на момент оформления."""
        return sum(i.quantity * i.price for i in self.items.all())


class OrderItem(models.Model):
    """Позиция заказа.

    Цена фиксируется в момент оформления: если потом цена товара изменится,
    заказ сохранит ту сумму, которую видел покупатель.
    """

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name='order_items',
        verbose_name='Товар')
    quantity = models.DecimalField(
        'Количество', max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0)])
    price = models.DecimalField(
        'Цена за единицу', max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0)])

    class Meta:
        db_table = 'order_items'
        verbose_name = 'Позиция заказа'
        verbose_name_plural = 'Позиции заказа'

    def __str__(self):
        return f'{self.product} × {self.quantity}'

    @property
    def sum(self):
        return self.quantity * self.price
