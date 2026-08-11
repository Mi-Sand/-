"""Проверка денежных расчётов: сумма прописью и НДС.

Ошибка здесь попадает прямо в подписанный документ и обнаруживается
позже всех — обычно уже у покупателя или в налоговой.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from billing.money import (amount_to_words, number_to_words, plural_form,
                           round_money, split_amount, vat_from_gross,
                           vat_on_net)


class PluralFormTest(SimpleTestCase):
    FORMS = ('рубль', 'рубля', 'рублей')

    def test_ones(self):
        for number in (1, 21, 101, 1001):
            with self.subTest(number=number):
                self.assertEqual(plural_form(number, self.FORMS), 'рубль')

    def test_few(self):
        for number in (2, 3, 4, 22, 33, 44, 102):
            with self.subTest(number=number):
                self.assertEqual(plural_form(number, self.FORMS), 'рубля')

    def test_many(self):
        for number in (0, 5, 9, 10, 20, 25, 100, 1000):
            with self.subTest(number=number):
                self.assertEqual(plural_form(number, self.FORMS), 'рублей')

    def test_teens_are_special(self):
        """11–14 требуют последнюю форму, хотя оканчиваются на 1–4."""
        for number in (11, 12, 13, 14, 111, 112, 1013):
            with self.subTest(number=number):
                self.assertEqual(plural_form(number, self.FORMS), 'рублей')


class NumberToWordsTest(SimpleTestCase):
    CASES = {
        0: 'ноль',
        1: 'один',
        2: 'два',
        10: 'десять',
        11: 'одиннадцать',
        19: 'девятнадцать',
        20: 'двадцать',
        21: 'двадцать один',
        99: 'девяносто девять',
        100: 'сто',
        101: 'сто один',
        110: 'сто десять',
        111: 'сто одиннадцать',
        200: 'двести',
        999: 'девятьсот девяносто девять',
        1000: 'одна тысяча',
        1001: 'одна тысяча один',
        2000: 'две тысячи',
        5000: 'пять тысяч',
        1234: 'одна тысяча двести тридцать четыре',
        11000: 'одиннадцать тысяч',
        21000: 'двадцать одна тысяча',
        100000: 'сто тысяч',
        1000000: 'один миллион',
        2000000: 'два миллиона',
        5000000: 'пять миллионов',
        1000000000: 'один миллиард',
        123456789: ('сто двадцать три миллиона четыреста пятьдесят шесть '
                    'тысяч семьсот восемьдесят девять'),
    }

    def test_known_numbers(self):
        for number, expected in self.CASES.items():
            with self.subTest(number=number):
                self.assertEqual(number_to_words(number), expected)

    def test_thousands_are_feminine(self):
        """«одна тысяча», не «один тысяча» — тысяча женского рода."""
        self.assertEqual(number_to_words(1000), 'одна тысяча')
        self.assertEqual(number_to_words(2000), 'две тысячи')

    def test_millions_are_masculine(self):
        self.assertEqual(number_to_words(1000000), 'один миллион')
        self.assertEqual(number_to_words(2000000), 'два миллиона')

    def test_zero_groups_skipped(self):
        """Пустые разряды не должны давать лишних слов."""
        self.assertEqual(number_to_words(1000001), 'один миллион один')
        self.assertEqual(number_to_words(1000000), 'один миллион')


class AmountToWordsTest(SimpleTestCase):
    def test_typical_amounts(self):
        cases = {
            Decimal('0.00'): 'Ноль рублей 00 копеек',
            Decimal('1.00'): 'Один рубль 00 копеек',
            Decimal('1.01'): 'Один рубль 01 копейка',
            Decimal('2.02'): 'Два рубля 02 копейки',
            Decimal('5.05'): 'Пять рублей 05 копеек',
            Decimal('1234.05'): ('Одна тысяча двести тридцать четыре рубля '
                                 '05 копеек'),
            Decimal('2300.00'): 'Две тысячи триста рублей 00 копеек',
            Decimal('11.11'): 'Одиннадцать рублей 11 копеек',
            Decimal('100000.50'): 'Сто тысяч рублей 50 копеек',
        }
        for amount, expected in cases.items():
            with self.subTest(amount=amount):
                self.assertEqual(amount_to_words(amount), expected)

    def test_starts_with_capital(self):
        self.assertTrue(amount_to_words(Decimal('50.00'))[0].isupper())

    def test_kopecks_always_two_digits(self):
        """05 копеек, а не 5 — в бланке принято два знака."""
        self.assertIn(' 05 ', amount_to_words(Decimal('10.05')))
        self.assertIn(' 50 ', amount_to_words(Decimal('10.50')))

    def test_rounds_to_kopecks(self):
        self.assertEqual(amount_to_words(Decimal('1.005')),
                         'Один рубль 01 копейка')

    def test_accepts_float_and_int(self):
        self.assertEqual(amount_to_words(100), 'Сто рублей 00 копеек')


class VatTest(SimpleTestCase):
    def test_extracted_not_added(self):
        """НДС выделяется из цены (20/120), а не начисляется сверх (20/100)."""
        self.assertEqual(vat_from_gross(Decimal('120.00'), 20),
                         Decimal('20.00'))
        self.assertEqual(vat_on_net(Decimal('100.00'), 20), Decimal('20.00'))

    def test_extraction_differs_from_addition(self):
        """Ошибиться способом — значит завысить налог на пятую часть."""
        gross = Decimal('2300.00')
        self.assertEqual(vat_from_gross(gross, 20), Decimal('383.33'))
        self.assertEqual(vat_on_net(gross, 20), Decimal('460.00'))

    def test_zero_and_none_rate(self):
        self.assertEqual(vat_from_gross(Decimal('100'), 0), Decimal('0.00'))
        self.assertEqual(vat_from_gross(Decimal('100'), None), Decimal('0.00'))

    def test_rounding_half_up(self):
        """Округление вверх на половине — как принято в бухгалтерии."""
        self.assertEqual(round_money(Decimal('0.005')), Decimal('0.01'))
        self.assertEqual(round_money(Decimal('0.014')), Decimal('0.01'))

    def test_split_with_vat_included(self):
        net, vat, total = split_amount(Decimal('120.00'), 20,
                                       vat_included=True)
        self.assertEqual((net, vat, total),
                         (Decimal('100.00'), Decimal('20.00'),
                          Decimal('120.00')))

    def test_split_with_vat_added(self):
        net, vat, total = split_amount(Decimal('100.00'), 20,
                                       vat_included=False)
        self.assertEqual((net, vat, total),
                         (Decimal('100.00'), Decimal('20.00'),
                          Decimal('120.00')))

    def test_split_parts_always_add_up(self):
        """Без налога + налог = всего. Иначе бланк не сойдётся."""
        for value in ('0.01', '1.00', '2300.00', '999.99', '12345.67'):
            for included in (True, False):
                with self.subTest(value=value, included=included):
                    net, vat, total = split_amount(
                        Decimal(value), 20, vat_included=included)
                    self.assertEqual(net + vat, total)

    def test_split_without_vat(self):
        net, vat, total = split_amount(Decimal('100.00'), None,
                                       vat_included=True)
        self.assertEqual((net, vat, total),
                         (Decimal('100.00'), Decimal('0.00'),
                          Decimal('100.00')))
