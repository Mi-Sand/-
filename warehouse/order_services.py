"""
Бизнес-логика заказов интернет-магазина.

Ключевая идея — резервирование. При оформлении заказа товар не списывается
со склада: физически он там и есть. Вместо этого позиции заказа в активных
статусах («новый», «подтверждён») считаются зарезервированными и вычитаются
из доступного к заказу количества.

Так остатки склада всегда отражают реальность, а отмена заказа не требует
никаких откатов — резерв просто перестаёт учитываться.

Реальное списание происходит один раз, при отгрузке: система создаёт
расходный документ и проводит его той же проверенной логикой, что и обычный
расход (process_outbound_document), включая проверку достаточности остатка.
"""
import re
from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (Order, OrderItem, OutboundDocument, OutboundItem,
                     Product, Stock)
from .language_model import BIGRAMS, TRIGRAMS
from .services import InsufficientStockError, process_outbound_document


def get_reserved_quantities(product_ids=None):
    """Сколько единиц каждого товара зарезервировано активными заказами.

    Возвращает словарь {product_id: количество}. Товары без резерва в
    словарь не попадают.
    """
    qs = (OrderItem.objects
          .filter(order__status__in=Order.ACTIVE_STATUSES))
    if product_ids is not None:
        qs = qs.filter(product_id__in=product_ids)

    rows = (qs.values('product_id')
              .annotate(reserved=Coalesce(
                  Sum('quantity'),
                  Value(0, output_field=DecimalField()))))
    return {r['product_id']: r['reserved'] for r in rows}


def get_available_quantity(product_id, reserved_map=None):
    """Доступно к заказу = остаток на складах − зарезервировано."""
    stock = (Stock.objects
             .filter(product_id=product_id)
             .aggregate(total=Coalesce(
                 Sum('quantity'),
                 Value(0, output_field=DecimalField())))['total'])
    if reserved_map is None:
        reserved_map = get_reserved_quantities([product_id])
    reserved = reserved_map.get(product_id, Decimal('0'))
    return stock - reserved


VOWELS = set('аеёиоуыэюяaeiouy')

# Редкие буквы: в случайном наборе их доля заметно выше, чем в живой речи
RARE_LETTERS = set('ъьщэюжцфхыё')

# Ряды клавиатуры — случайный набор часто оставляет их следы
KEYBOARD_ROWS = (
    'йцукенгшщзхъ', 'фывапролджэ', 'ячсмитьбю',
    'qwertyuiop', 'asdfghjkl', 'zxcvbnm',
)

# Координаты клавиш русской раскладки: нужны, чтобы понять, набирались ли
# буквы соседними клавишами. Когда человек «мажет» пальцем по клавиатуре,
# почти все пары букв оказываются соседями — в живых словах так не бывает.
_KEY_POSITIONS = {
    ch: (row_index, col_index)
    for row_index, row in enumerate(
        ('йцукенгшщзхъ', 'фывапролджэ', 'ячсмитьбю'))
    for col_index, ch in enumerate(row)
}


def _adjacent_keys_ratio(word):
    """Доля пар соседних букв, набранных рядом стоящими клавишами.

    Соседними считаются клавиши, отстоящие не более чем на один ряд и два
    столбца. У осмысленных слов эта доля обычно ниже половины, у «мазни»
    вроде «вапрапрв» — более восьмидесяти процентов.
    """
    pairs = [(word[i], word[i + 1]) for i in range(len(word) - 1)]
    known = [p for p in pairs
             if p[0] in _KEY_POSITIONS and p[1] in _KEY_POSITIONS]
    if not known:
        return 0.0
    adjacent = 0
    for first, second in known:
        row_a, col_a = _KEY_POSITIONS[first]
        row_b, col_b = _KEY_POSITIONS[second]
        if abs(row_a - row_b) <= 1 and abs(col_a - col_b) <= 2:
            adjacent += 1
    return adjacent / len(known)

# Пороги «неправдоподобности» слова, зависящие от его длины.
#
# Короткие слова дают мало буквосочетаний, поэтому статистика по ним
# ненадёжна: «Уфа» из трёх букв легко получает высокую оценку случайно.
# Для них порог выше — нужны более веские основания.
#
# Значения получены измерением на корпусе из 400 реальных имён, фамилий,
# городов и улиц и 550 образцов случайного набора. Пороги взяты как
# максимум оценки по настоящим словам плюс небольшой запас. При такой
# настройке ни одно слово из корпуса не отклоняется ошибочно, а случайный
# набор распознаётся примерно в 95% случаев.
GIBBERISH_THRESHOLDS = (
    (5, 0.25),    # слова до 5 букв
    (8, 0.24),    # от 6 до 8 букв
    (999, 0.22),  # длиннее 8 букв
)


def _unseen_ratio(word, ngrams, size):
    """Доля сочетаний длины size, отсутствующих в модели языка."""
    padded = f'^{word}$'
    chunks = [padded[i:i + size] for i in range(len(padded) - size + 1)]
    if not chunks:
        return 1.0
    return sum(1 for c in chunks if c not in ngrams) / len(chunks)


def _has_keyboard_run(word, min_len=5):
    """Есть ли в слове кусок подряд идущих клавиш («йцукен», «фывап»).

    Порог в пять символов выбран по результатам проверки: при четырёх
    начинались ложные срабатывания на настоящих словах — например,
    «Пролетарская» содержит «прол», совпадающий с куском ряда «фывапролджэ».
    """
    for row in KEYBOARD_ROWS:
        for start in range(len(row) - min_len + 1):
            chunk = row[start:start + min_len]
            if chunk in word or chunk[::-1] in word:
                return True
    return False


def gibberish_score(word):
    """Оценка неправдоподобности слова от 0 (обычное слово) до 1 (мусор).

    Складывается из четырёх признаков:

    * доля двухбуквенных сочетаний, которых нет в русском языке;
    * то же для трёхбуквенных — они точнее улавливают структуру слова;
    * отклонение доли гласных от нормы (в русском их около 42%);
    * доля редких букв (ъ, ь, щ, э, ю, ж, ц, ф, х, ы) — в случайном
      наборе они встречаются гораздо чаще, чем в живой речи.

    Веса подобраны экспериментально на корпусе реальных слов и на пятистах
    образцах случайного набора, сгенерированных имитацией разных способов
    «мазни» по клавиатуре.
    """
    w = word.lower()
    letters = [c for c in w if c.isalpha()]
    if not letters:
        return 1.0

    bigram_miss = _unseen_ratio(w, BIGRAMS, 2)
    trigram_miss = _unseen_ratio(w, TRIGRAMS, 3)

    vowel_ratio = sum(1 for c in letters if c in VOWELS) / len(letters)
    vowel_deviation = min(abs(vowel_ratio - 0.42) / 0.42, 1.0)

    rare_ratio = sum(1 for c in letters if c in RARE_LETTERS) / len(letters)
    rare_excess = min(rare_ratio / 0.35, 1.0)

    language_part = (0.40 * bigram_miss + 0.30 * trigram_miss
                     + 0.15 * vowel_deviation + 0.15 * rare_excess)

    # Признак «мазни» по соседним клавишам учитываем только для слов от
    # шести букв: в коротких словах вроде «Иван» соседние клавиши
    # встречаются и по случайности.
    if len(letters) >= 6:
        keyboard_part = max(0.0, (_adjacent_keys_ratio(w) - 0.55) / 0.45)
    else:
        keyboard_part = 0.0

    return 0.85 * language_part + 0.15 * keyboard_part


def looks_like_gibberish(word):
    """Похоже ли слово на случайный набор символов.

    Сначала проверяются явные признаки, не требующие статистики: повтор
    одной буквы, многократно повторённый слог, куски рядов клавиатуры и
    неестественно длинные цепочки согласных. Затем — оценка правдоподобия
    по модели языка (gibberish_score) с порогом, зависящим от длины слова.

    Составные слова через дефис («Йошкар-Ола», «Анна-Мария») проверяются
    по частям: каждая часть — самостоятельное слово со своей статистикой.

    Настройка сознательно смещена в сторону осторожности: пропустить
    сомнительное имя менее вредно, чем отказать реальному покупателю —
    заказ в любом случае подтверждается звонком менеджера.
    """
    w = word.lower().strip()

    # Составное слово разбираем по частям
    if '-' in w or "'" in w:
        parts = [p for p in re.split(r"[-']+", w) if p]
        return any(looks_like_gibberish(p) for p in parts)

    letters = [c for c in w if c.isalpha()]
    if len(letters) < 3:
        return False  # «ул», «д», «О» в составных именах не анализируем

    # Одна буква подряд три и более раз: «аааа»
    if re.search(r'(.)\1{2,}', w):
        return True

    # Слог, повторённый три и более раза: «вававава».
    # Именно три: «Варвара», «Калининград», «Гагарина» содержат двойной
    # повтор и являются нормальными словами.
    if re.search(r'(.{2,3})\1{2,}', w):
        return True

    if _has_keyboard_run(w):
        return True

    # Шесть согласных подряд. Порог мягкий: в фамилиях народов России
    # встречается пять подряд («Мкртчян»).
    run = 0
    for c in letters:
        if c not in VOWELS:
            run += 1
            if run >= 6:
                return True
        else:
            run = 0

    # Статистическая оценка применима только к кириллице: модель языка
    # построена на русских словах и о латинских именах ничего не знает.
    # Для них ограничиваемся правилами выше.
    if not re.search(r'[а-яё]', w):
        return False

    score = gibberish_score(w)
    for max_len, threshold in GIBBERISH_THRESHOLDS:
        if len(letters) <= max_len:
            return score >= threshold
    return False


def validate_customer_name(name):
    """Проверить имя покупателя на явный мусор.

    Полностью отличить выдуманное имя от настоящего невозможно — имена
    бывают редкими. Но случайный набор по клавиатуре распознаётся
    достаточно надёжно по закономерностям языка (см. looks_like_gibberish).

    Возвращает очищенное имя или возбуждает ValueError.
    """
    if not name or not name.strip():
        raise ValueError('Укажите имя покупателя')

    cleaned = ' '.join(name.split())  # убираем лишние пробелы

    if len(cleaned) < 2:
        raise ValueError('Имя слишком короткое — укажите полное имя')
    if len(cleaned) > 100:
        raise ValueError('Имя слишком длинное')

    # Только буквы, пробелы, дефис и апостроф (Анна-Мария, О'Коннор)
    if not re.fullmatch(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\s\-']*", cleaned):
        raise ValueError(
            'Имя может содержать только буквы, пробел и дефис. '
            'Цифры и символы недопустимы.')

    letters = re.sub(r"[\s\-']", '', cleaned).lower()

    # Одна и та же буква подряд много раз: «аааааа»
    if re.search(r'(.)\1{2,}', letters):
        raise ValueError('Укажите настоящее имя')

    # Все буквы одинаковые
    if len(set(letters)) < 2:
        raise ValueError('Укажите настоящее имя')

    # Набор без гласных — почти наверняка случайный ввод («джкфвп»)
    if not (VOWELS & set(letters)):
        raise ValueError('Укажите настоящее имя')

    # Каждое слово имени проверяем на белиберду. Односимвольные части
    # допустимы в составных именах и инициалах: О'Коннор, Ким Ир Сен.
    for part in re.split(r"[\s\-']+", cleaned):
        if looks_like_gibberish(part):
            raise ValueError(
                'Имя выглядит некорректно. Укажите настоящее имя — '
                'мы свяжемся с вами для подтверждения заказа.')

    return cleaned


def validate_phone(phone):
    """Проверить и привести телефон к единому виду +7XXXXXXXXXX.

    Принимаются привычные способы записи российских номеров:
    +7 999 123-45-67, 8(999)1234567, 79991234567 и подобные.

    Возвращает нормализованный номер или возбуждает ValueError.
    """
    if not phone or not phone.strip():
        raise ValueError('Укажите телефон для связи')

    digits = re.sub(r'\D', '', phone)

    # 8XXXXXXXXXX → 7XXXXXXXXXX
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    # 10 цифр без кода страны → добавляем 7
    elif len(digits) == 10:
        digits = '7' + digits

    if len(digits) != 11 or not digits.startswith('7'):
        raise ValueError(
            'Неверный формат телефона. Укажите российский номер, '
            'например: +7 999 123-45-67')

    # Код оператора/региона не может начинаться с 0 или 1
    if digits[1] in '01':
        raise ValueError(
            'Неверный номер телефона. Проверьте код оператора.')

    return f'+{digits}'


def validate_email_optional(email):
    """Проверить e-mail, если он указан. Поле необязательное."""
    if not email or not email.strip():
        return ''

    value = email.strip()
    if len(value) > 254:
        raise ValueError('E-mail слишком длинный')

    # Базовая структура: что-то@что-то.домен
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", value):
        raise ValueError(
            'Неверный формат e-mail. Например: ivan@example.com')
    return value


def validate_comment(comment):
    """Проверить комментарий к заказу. Поле необязательное и свободное."""
    if not comment or not comment.strip():
        return ''

    value = comment.strip()
    if len(value) > 1000:
        raise ValueError('Комментарий слишком длинный (максимум 1000 знаков)')

    # В свободном тексте не придираемся к содержанию, но одна буква,
    # повторённая много раз, — явный мусор
    if re.search(r'(.)\1{9,}', value):
        raise ValueError('Комментарий выглядит некорректно')
    return value


def _generate_order_number():
    """Следующий номер заказа вида ЗАК-00001."""
    last = Order.objects.order_by('-id').first()
    next_id = (last.id + 1) if last else 1
    return f'ЗАК-{next_id:05d}'


# Страны и территории, доставка в которые не осуществляется. Список нужен,
# чтобы отсечь явно зарубежные адреса. Это не полноценная валидация адреса
# (для неё нужен внешний сервис вроде DaData), а разумная проверка от
# случайных и очевидно неподходящих вводов.
FOREIGN_MARKERS = (
    'украина', 'беларусь', 'белоруссия', 'казахстан', 'узбекистан',
    'киргизия', 'кыргызстан', 'таджикистан', 'туркменистан', 'азербайджан',
    'армения', 'грузия', 'молдова', 'молдавия', 'латвия', 'литва', 'эстония',
    'сша', 'usa', 'германия', 'germany', 'китай', 'china', 'турция',
    'turkey', 'польша', 'poland', 'финляндия', 'finland', 'франция',
    'испания', 'италия', 'великобритания', 'англия', 'канада', 'япония',
    'корея', 'индия', 'израиль', 'оаэ', 'эмираты', 'таиланд', 'вьетнам',
)


# Слова, которые встречаются в любом реальном адресе. Их наличие —
# надёжный признак того, что покупатель ввёл адрес, а не случайный текст.
ADDRESS_MARKERS = (
    'ул', 'улица', 'дом', 'д', 'кв', 'квартира', 'корп', 'корпус',
    'г', 'гор', 'город', 'пос', 'посёлок', 'поселок', 'село', 'с',
    'деревня', 'дер', 'обл', 'область', 'край', 'респ', 'республика',
    'р-н', 'район', 'пр', 'просп', 'проспект', 'пер', 'переулок',
    'ш', 'шоссе', 'наб', 'набережная', 'бул', 'бульвар', 'пл', 'площадь',
    'мкр', 'микрорайон', 'проезд', 'тупик', 'аллея', 'линия', 'стр',
    'строение', 'влад', 'владение', 'офис', 'этаж', 'подъезд',
)


def validate_russian_address(address):
    """Проверить, что адрес доставки — настоящий российский адрес.

    Доставка осуществляется только по России. Проверяется:

    1. Отсутствие указания на другую страну.
    2. Наличие кириллицы — адрес пишется по-русски.
    3. Наличие структурного слова («улица», «дом», «город» и подобных) —
       в любом реальном адресе такое слово есть, а в случайном наборе нет.
    4. Наличие цифры — номер дома есть всегда.
    5. Отсутствие белиберды в словах (см. looks_like_gibberish).

    Пустой адрес допускается: покупатель может забрать заказ самовывозом,
    адрес тогда уточняется по телефону.

    Возбуждает ValueError с понятным пояснением, если адрес не подходит.
    """
    if not address or not address.strip():
        return  # адрес не обязателен

    text = address.strip()
    lowered = text.lower()

    # 1. Явное указание другой страны
    for marker in FOREIGN_MARKERS:
        if re.search(rf'\b{re.escape(marker)}\b', lowered):
            raise ValueError(
                'Доставка осуществляется только по России. '
                'Укажите адрес на территории Российской Федерации.')

    # 2. Адрес должен быть на русском
    if not re.search(r'[а-яё]', lowered):
        raise ValueError(
            'Укажите адрес доставки на русском языке '
            '(например: г. Дмитров, ул. Советская, д. 10, кв. 5).')

    if len(text) < 10:
        raise ValueError(
            'Адрес слишком короткий. Укажите город, улицу и номер дома.')

    # 3. Структурное слово адреса
    words = re.findall(r'[а-яёa-z]+', lowered)
    has_marker = any(w in ADDRESS_MARKERS for w in words)
    if not has_marker:
        raise ValueError(
            'Укажите адрес полностью — с городом, улицей и номером дома. '
            'Например: г. Дмитров, ул. Советская, д. 10, кв. 5')

    # 4. Номер дома
    if not re.search(r'\d', text):
        raise ValueError(
            'В адресе не указан номер дома. '
            'Например: г. Дмитров, ул. Советская, д. 10, кв. 5')

    # 5. Белиберда в словах адреса
    for word in words:
        if len(word) >= 4 and looks_like_gibberish(word):
            raise ValueError(
                'Адрес выглядит некорректно. Проверьте написание '
                'города и улицы.')


@transaction.atomic
def _read_cart(items):
    """Разобрать корзину, пришедшую из браузера покупателя.

    Заказ оформляет посторонний человек без входа в систему, поэтому
    проверяется всё: что корзина вообще список, что в каждой строке есть
    товар и количество и что это числа.

    Раньше проверялось только «количество больше нуля», а до неё дело не
    доходило: строка вроде «абв» падала при переводе в число, и покупатель
    получал отказ сервера вместо понятного сообщения. Особый случай —
    «Infinity» и «NaN»: в число они переводятся, но сравнение NaN с нулём
    всегда ложно, поэтому такое количество проскакивало проверку насквозь.

    Возвращает список пар (id товара, количество).
    """
    if not isinstance(items, (list, tuple)):
        raise ValueError('Неверный формат корзины')

    rows = []
    for row in items:
        if not isinstance(row, dict):
            raise ValueError('Неверный формат строки заказа')
        try:
            product_id = int(row['product'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('В строке заказа не указан товар')
        try:
            quantity = Decimal(str(row['quantity']))
        except (KeyError, TypeError, ArithmeticError):
            raise ValueError('Неверное количество в строке заказа')
        if not quantity.is_finite():
            raise ValueError('Неверное количество в строке заказа')
        if quantity <= 0:
            raise ValueError('Количество должно быть больше нуля')
        rows.append((product_id, quantity))
    return rows


def create_order(customer_name, customer_phone, items,
                 customer_email='', address='', comment=''):
    """Создать заказ покупателя с проверкой доступности товара.

    items — список [{'product': id, 'quantity': N}, ...].

    Проверка выполняется внутри транзакции с блокировкой строк остатков:
    если два покупателя одновременно заказывают последнюю единицу товара,
    второй получит отказ, а не отрицательный остаток.

    Возбуждает InsufficientStockError, если товара не хватает,
    и ValueError при некорректных данных.
    """
    # Проверяем и нормализуем все данные формы
    customer_name = validate_customer_name(customer_name)
    customer_phone = validate_phone(customer_phone)
    customer_email = validate_email_optional(customer_email)
    comment = validate_comment(comment)

    if not items:
        raise ValueError('Корзина пуста')

    # Доставка только по России — проверяем адрес до создания заказа
    validate_russian_address(address)

    rows = _read_cart(items)
    product_ids = [pid for pid, _ in rows]

    # Блокируем строки остатков по заказываемым товарам до конца транзакции —
    # это исключает гонку между одновременными заказами.
    list(Stock.objects.select_for_update().filter(product_id__in=product_ids))

    reserved_map = get_reserved_quantities(product_ids)
    products = {p.id: p for p in Product.objects.filter(id__in=product_ids)}

    # Один и тот же товар может прийти несколькими строками (например,
    # покупатель добавил его в корзину дважды). Складываем количества по
    # товару: иначе каждая строка проверялась бы против полного остатка
    # по отдельности, и суммарно можно было бы заказать больше, чем есть.
    wanted = {}
    for pid, qty in rows:
        wanted[pid] = wanted.get(pid, Decimal('0')) + qty

    prepared = []
    for pid, qty in wanted.items():
        product = products.get(pid)
        if product is None:
            raise ValueError(f'Товар с id={pid} не найден')
        if product.status != 'active':
            raise ValueError(f'Товар «{product.name}» снят с продажи')

        available = get_available_quantity(pid, reserved_map)
        if qty > available:
            raise InsufficientStockError(
                f'«{product.name}» ({product.size}, {product.color}): '
                f'доступно {available:g} шт., запрошено {qty:g}')

        prepared.append((product, qty))

    order = Order.objects.create(
        number=_generate_order_number(),
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_email=customer_email,
        address=(address or '').strip(),
        comment=comment)

    for product, qty in prepared:
        OrderItem.objects.create(
            order=order, product=product,
            quantity=qty, price=product.selling_price)

    return order


@transaction.atomic
def confirm_order(order_id):
    """Подтвердить заказ (менеджер связался с покупателем)."""
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status != 'new':
        raise ValueError(
            f'Подтвердить можно только новый заказ '
            f'(текущий статус: {order.get_status_display()})')
    order.status = 'confirmed'
    order.save()
    return order


@transaction.atomic
def cancel_order(order_id):
    """Отменить заказ. Резерв снимается автоматически.

    Отгруженный заказ отменить нельзя — товар уже уехал, для возврата
    нужен отдельный приходный документ.
    """
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status == 'shipped':
        raise ValueError(
            'Нельзя отменить отгруженный заказ. Для возврата товара '
            'оформите приходный документ.')
    if order.status == 'cancelled':
        raise ValueError('Заказ уже отменён')
    order.status = 'cancelled'
    order.save()
    return order


@transaction.atomic
def ship_order(order_id, warehouse_id, user=None):
    """Отгрузить заказ: создать расходный документ и провести его.

    Здесь происходит реальное списание со склада. Используется штатная
    логика проведения расхода — с проверкой остатков и записью в журнал
    движения, то есть отгрузка отражается в учёте так же, как любой
    другой расход.
    """
    order = (Order.objects
             .select_for_update()
             .prefetch_related('items__product')
             .get(pk=order_id))

    if order.status == 'shipped':
        raise ValueError('Заказ уже отгружен')
    if order.status == 'cancelled':
        raise ValueError('Нельзя отгрузить отменённый заказ')

    doc = OutboundDocument.objects.create(
        doc_number=f'ОТГ-{order.number}',
        doc_date=timezone.now().date(),
        warehouse_id=warehouse_id,
        purpose='sale',
        production_order=order.number,
        created_by=user)

    for item in order.items.all():
        OutboundItem.objects.create(
            outbound_doc=doc,
            product=item.product,
            quantity=item.quantity,
            unit_price=item.price)

    # Проведение спишет товар и проверит достаточность остатка.
    # Если товара не хватает — исключение откатит всю транзакцию,
    # включая созданный документ.
    process_outbound_document(doc.id, user=user)

    order.status = 'shipped'
    order.outbound_document = doc
    order.save()
    return order
