"""Штрихкоды и этикетки.

Приёмка шла глазами и руками: кладовщик искал позицию в списке по
названию. Со сканером он подносит прибор к коробке — но для этого на
коробке должна быть этикетка, а в системе должен быть код.

Проверки кодировщика опираются на устройство Code 128: каждый знак —
одиннадцать модулей, знак остановки — тринадцать, и есть контрольное
число. Сходимость с настоящим сканером проверена отдельно, вне этих
проверок: код, отрисованный этим кодировщиком, прочитан библиотекой
распознавания и совпал со строкой.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from .barcode import (BarcodeError, MAX_LENGTH, PATTERNS, START_B, suggest,
                      svg, values, widths)
from .models import Material, Product

User = get_user_model()


class EncodingTest(TestCase):
    """Устройство кода."""

    def test_every_symbol_is_eleven_modules(self):
        """Кроме знака остановки — у него тринадцать.

        Это единственная проверка, которая ловит опечатку в таблице
        полос: неверная строка даст код, который сканер не прочтёт, а
        глазами разницы не видно.
        """
        for number, pattern in enumerate(PATTERNS):
            total = sum(int(width) for width in pattern)
            expected = 13 if number == 106 else 11
            self.assertEqual(total, expected, f'значение {number}')

    def test_total_length_is_predictable(self):
        for text in ('A', 'HI345678', 'SKLAD-000042'):
            modules = sum(widths(text))
            self.assertEqual(modules, (len(text) + 2) * 11 + 13, text)

    def test_checksum_is_counted(self):
        """Без контрольного числа сканер принял бы за код любую грязь."""
        codes = values('HI345678')
        expected = (START_B + sum((i + 1) * c
                                  for i, c in enumerate(codes))) % 103
        bars = widths('HI345678')
        checksum_pattern = ''.join(
            str(width) for width in bars[-13:-7])
        self.assertEqual(checksum_pattern, PATTERNS[expected])

    def test_cyrillic_is_refused_with_an_explanation(self):
        """Русские буквы не берёт ни один штрихкод — и об этом надо
        сказать прямо, а не выдать пустую этикетку."""
        with self.assertRaises(BarcodeError) as caught:
            values('Кожа')
        self.assertIn('русские', str(caught.exception).lower())

    def test_empty_is_refused(self):
        with self.assertRaises(BarcodeError):
            values('')

    def test_too_long_is_refused(self):
        with self.assertRaises(BarcodeError) as caught:
            values('A' * (MAX_LENGTH + 1))
        self.assertIn('длинный', str(caught.exception))


class DrawingTest(TestCase):
    """Рисунок."""

    def test_svg_is_produced(self):
        picture = svg('MAT00000012')
        self.assertTrue(picture.startswith('<svg'))
        self.assertIn('<rect', picture)

    def test_caption_is_escaped(self):
        """Подпись берут из названия, а его вводит человек."""
        picture = svg('AB123', caption='Мяч <b>большой</b>')
        self.assertNotIn('<b>', picture)
        self.assertIn('&lt;b&gt;', picture)

    def test_narrow_bar_is_wide_enough(self):
        """Тоньше 1.2 точки сканеры ошибаются на обычной бумаге."""
        picture = svg('AB123')
        first = picture.split('width="', 2)[2]
        self.assertGreaterEqual(float(first.split('"')[0]), 1.2)

    def test_suggested_code_is_encodable(self):
        code = suggest('MAT', 12)
        self.assertEqual(code, 'MAT00000012')
        self.assertTrue(svg(code).startswith('<svg'))


class LabelPageTest(TestCase):
    """Лист этикеток."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='klad', password='x', role='storekeeper')
        self.client.force_login(self.user)
        self.material = Material.objects.create(
            name='Кожа хромовая', unit='m', category='leather',
            article_number='КЖ-01', reorder_point=Decimal('50'))
        self.product = Product.objects.create(
            article_number='МЯ-1', name='Мяч футбольный', category='equipment',
            size='5', color='белый', cost=Decimal('300'),
            selling_price=Decimal('1500'))

    def test_label_for_material(self):
        page = self.client.get(f'/print/labels/?material={self.material.pk}')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Кожа хромовая')
        self.assertContains(page, '<svg')

    def test_position_without_barcode_gets_an_internal_number(self):
        """Покупать диапазон у регистратора нужно только тому, кто
        отдаёт товар в чужие магазины."""
        page = self.client.get(f'/print/labels/?material={self.material.pk}')
        self.assertContains(page, f'MAT{self.material.pk:08d}')

    def test_own_barcode_wins(self):
        self.material.barcode = 'ABC-9012'
        self.material.save()
        page = self.client.get(f'/print/labels/?material={self.material.pk}')
        self.assertContains(page, 'ABC-9012')

    def test_several_copies(self):
        page = self.client.get(
            f'/print/labels/?material={self.material.pk}&count=5')
        self.assertEqual(page.content.decode().count('class="label"'), 5)

    def test_count_is_limited(self):
        """Опечатка в адресе не должна печатать до утра."""
        page = self.client.get(
            f'/print/labels/?material={self.material.pk}&count=100000')
        self.assertLessEqual(
            page.content.decode().count('class="label"'), 100)

    def test_several_positions_at_once(self):
        page = self.client.get(
            f'/print/labels/?material={self.material.pk}'
            f'&product={self.product.pk}')
        self.assertContains(page, 'Кожа хромовая')
        self.assertContains(page, 'Мяч футбольный')

    def test_nonsense_in_the_address_does_not_break_the_page(self):
        page = self.client.get('/print/labels/?material=абв&product=-1')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Нечего печатать')

    def test_missing_position_is_skipped(self):
        page = self.client.get('/print/labels/?material=999999')
        self.assertEqual(page.status_code, 200)

    def test_unencodable_barcode_does_not_lose_the_whole_sheet(self):
        """Одна негодная позиция не должна оставить кладовщика без листа."""
        self.material.barcode = 'Кожа'
        self.material.save()
        page = self.client.get(
            f'/print/labels/?material={self.material.pk}'
            f'&product={self.product.pk}')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Мяч футбольный')
        self.assertContains(page, 'русские')

    def test_stranger_cannot_print(self):
        self.client.logout()
        page = self.client.get(f'/print/labels/?material={self.material.pk}')
        self.assertEqual(page.status_code, 302)


class BarcodeSearchTest(TestCase):
    """Поиск по коду — то, ради чего сканер и нужен."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='klad', password='x', role='storekeeper')
        self.client.force_login(self.user)
        Material.objects.create(
            name='Кожа хромовая', unit='m', category='leather',
            barcode='4601234567890', reorder_point=Decimal('50'))

    def test_material_is_found_by_barcode(self):
        found = self.client.get('/api/materials/?search=4601234567890').json()
        self.assertEqual(found['count'], 1)
        self.assertEqual(found['results'][0]['name'], 'Кожа хромовая')

    def test_barcode_is_saved(self):
        answer = self.client.post('/api/materials/', {
            'name': 'Нить белая', 'unit': 'm', 'category': 'textile',
            'reorder_point': '500', 'barcode': '4600000000017'},
            content_type='application/json')
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(Material.objects.get(name='Нить белая').barcode,
                         '4600000000017')
