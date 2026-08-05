"""
Денежные величины в документах: сумма прописью и расчёт НДС.

Обе задачи выглядят мелкими, но ошибка в любой из них попадает прямо в
подписанный документ. Сумма прописью — обязательный реквизит счёта, и
расхождение с цифрами делает документ спорным. НДС, посчитанный не тем
способом, расходится на копейки с расчётом покупателя, и бухгалтерии
приходится объясняться.

Поэтому здесь всё считается на Decimal — не на числах с плавающей
точкой, где 0.1 + 0.2 не равно 0.3, — и округляется банковским правилом
«половина к большему», как принято в бухгалтерском учёте.
"""
from decimal import Decimal, ROUND_HALF_UP

# --- Числительные -----------------------------------------------------------
# Единицы даны в двух родах: рубли мужского рода («один рубль»), а тысячи
# женского («одна тысяча»). Без этого получилось бы «один тысяча».
UNITS_MASCULINE = (
    '', 'один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь',
    'девять')
UNITS_FEMININE = (
    '', 'одна', 'две', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь',
    'девять')

TEENS = (
    'десять', 'одиннадцать', 'двенадцать', 'тринадцать', 'четырнадцать',
    'пятнадцать', 'шестнадцать', 'семнадцать', 'восемнадцать',
    'девятнадцать')

TENS = (
    '', '', 'двадцать', 'тридцать', 'сорок', 'пятьдесят', 'шестьдесят',
    'семьдесят', 'восемьдесят', 'девяносто')

HUNDREDS = (
    '', 'сто', 'двести', 'триста', 'четыреста', 'пятьсот', 'шестьсот',
    'семьсот', 'восемьсот', 'девятьсот')

# Разряды: (формы слова, женский ли род числительного при нём).
#
# Род задаётся словом, которое стоит следом. Единицы идут перед названием
# валюты — «один рубль», мужской род. Тысяча женского рода: «одна тысяча»,
# «две тысячи». Миллион и миллиард снова мужского.
SCALES = (
    (('', '', ''), False),                             # единицы: один рубль
    (('тысяча', 'тысячи', 'тысяч'), True),             # одна тысяча
    (('миллион', 'миллиона', 'миллионов'), False),
    (('миллиард', 'миллиарда', 'миллиардов'), False),
)

RUBLE_FORMS = ('рубль', 'рубля', 'рублей')
KOPECK_FORMS = ('копейка', 'копейки', 'копеек')


def plural_form(number, forms):
    """Выбрать форму слова под число: 1 рубль, 2 рубля, 5 рублей.

    Правило русского языка: числа на 11–14 всегда требуют последнюю форму
    («одиннадцать рублей»), поэтому остаток от ста проверяется раньше
    остатка от десяти.
    """
    one, few, many = forms
    hundred_remainder = abs(number) % 100
    if 11 <= hundred_remainder <= 14:
        return many
    ten_remainder = hundred_remainder % 10
    if ten_remainder == 1:
        return one
    if 2 <= ten_remainder <= 4:
        return few
    return many


def _group_to_words(group, feminine):
    """Записать словами число от 1 до 999."""
    words = []
    hundreds, remainder = divmod(group, 100)
    if hundreds:
        words.append(HUNDREDS[hundreds])

    tens, units = divmod(remainder, 10)
    if tens == 1:
        # 10–19 — отдельные слова, на десятки и единицы не раскладываются
        words.append(TEENS[units])
    else:
        if tens:
            words.append(TENS[tens])
        if units:
            table = UNITS_FEMININE if feminine else UNITS_MASCULINE
            words.append(table[units])
    return words


def number_to_words(number):
    """Записать целое число словами: 1234 → «одна тысяча двести тридцать четыре»."""
    number = int(number)
    if number == 0:
        return 'ноль'

    words = []
    if number < 0:
        words.append('минус')
        number = -number

    # Разбираем число на группы по три цифры, начиная со старших
    groups = []
    while number > 0:
        number, group = divmod(number, 1000)
        groups.append(group)

    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if group == 0:
            continue
        forms, feminine = SCALES[index] if index < len(SCALES) else SCALES[-1]
        words.extend(_group_to_words(group, feminine))
        if index > 0:
            words.append(plural_form(group, forms))

    return ' '.join(words)


def amount_to_words(amount):
    """Сумма прописью для документа.

    Например: Decimal('1234.05') → «Одна тысяча двести тридцать четыре
    рубля 05 копеек».

    Копейки принято печатать цифрами — так принято в бланках и так
    меньше поводов для разночтений.
    """
    amount = Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    negative = amount < 0
    amount = abs(amount)

    rubles = int(amount)
    kopecks = int((amount - rubles) * 100)

    words = number_to_words(rubles)
    text = (f'{words} {plural_form(rubles, RUBLE_FORMS)} '
            f'{kopecks:02d} {plural_form(kopecks, KOPECK_FORMS)}')
    if negative:
        text = f'минус {text}'
    # С заглавной буквы: так эта строка выглядит в бланках
    return text[0].upper() + text[1:]


def round_money(value):
    """Округлить до копеек по правилу «половина к большему»."""
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def vat_from_gross(gross, rate):
    """Выделить НДС из суммы, в которую он уже включён.

    Именно выделить, а не начислить: в рознице цена на ценнике — конечная,
    налог сидит внутри неё. Формула — сумма × ставка / (100 + ставка):
    для 20% это 20/120, то есть примерно одна шестая, а не одна пятая.
    Начисление сверху дало бы завышенный налог и расхождение с суммой,
    которую покупатель видел на витрине.
    """
    if rate is None:
        return Decimal('0.00')
    gross = Decimal(gross)
    rate = Decimal(rate)
    if rate == 0:
        return Decimal('0.00')
    return round_money(gross * rate / (Decimal('100') + rate))


def vat_on_net(net, rate):
    """Начислить НДС сверх цены, в которую он не включён."""
    if rate is None:
        return Decimal('0.00')
    net = Decimal(net)
    rate = Decimal(rate)
    return round_money(net * rate / Decimal('100'))


def split_amount(gross_or_net, rate, vat_included):
    """Разложить сумму на «без налога», «налог» и «всего».

    Возвращает три величины независимо от того, включён налог в цену или
    начисляется сверх, — бланкам нужны все три, а способ расчёта у них
    один и тот же.
    """
    value = Decimal(gross_or_net)
    if rate is None:
        return round_money(value), Decimal('0.00'), round_money(value)

    if vat_included:
        total = round_money(value)
        vat = vat_from_gross(total, rate)
        return round_money(total - vat), vat, total

    net = round_money(value)
    vat = vat_on_net(net, rate)
    return net, vat, round_money(net + vat)
