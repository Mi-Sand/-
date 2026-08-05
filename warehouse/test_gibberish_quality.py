"""Контроль качества детектора белиберды.

Эти тесты фиксируют достигнутый уровень: детектор не должен отклонять
настоящие русские слова и обязан отсекать случайный набор с клавиатуры.
Если при будущих правках качество упадёт, тесты это покажут.
"""
import random

from django.test import SimpleTestCase

from warehouse.order_services import looks_like_gibberish

# Настоящие слова: имена (в том числе редкие), фамилии народов России,
# города, улицы. Ни одно не должно быть отклонено.
REAL_WORDS = """
Иван Пётр Александр Михаил Владислав Вячеслав Святослав Анастасия Екатерина
Аглая Аполлинария Евлампий Мефодий Прокофий Агафья Устинья Феврония Ефросинья
Гульнара Айгуль Мадина Рустам Тимур Заур Хабиб Ильдар Эльдар Арсен Ашот
Иванов Смирнов Кузнецов Щербаков Мкртчян Оганесян Дзюба Хайруллина Тхагапсов
Гаджимурадов Нурмагомедов Бердымухамедов Ким Цой Пак
Москва Дмитров Мытищи Уфа Казань Якутск Тетюши Мамадыш Кувандык Нижневартовск
Санкт-Петербург Екатеринбург Комсомольск Йошкар-Ола Стерлитамак Верхнеуральск
Советская Комсомольская Пролетарская Профсоюзная Молодёжная Набережная
Мелиораторов Приборостроителей Судостроительная Авиаконструкторов
""".split()

# Заведомая белиберда — примеры из реальной практики
JUNK_WORDS = """
тщжоджохжэлд вававпвапваыпуф фывфыв йцукенгш ждлоаывп щзхъфы пролджэ
ячсмитьбю кывдлоа хъфывапр нгшщзх джыфва выапролд шщзхъфыв тьбюжэх
""".split()


class GibberishDetectorQualityTest(SimpleTestCase):
    """Проверка качества на фиксированных наборах."""

    def test_no_false_positives_on_real_words(self):
        """Ни одно настоящее слово не должно быть отклонено."""
        rejected = [w for w in REAL_WORDS if looks_like_gibberish(w)]
        self.assertEqual(
            rejected, [],
            f'Настоящие слова ошибочно признаны белибердой: {rejected}')

    def test_catches_known_junk(self):
        """Известные образцы белиберды должны отсекаться."""
        missed = [w for w in JUNK_WORDS if not looks_like_gibberish(w)]
        self.assertEqual(
            missed, [],
            f'Белиберда не распознана: {missed}')

    def test_catches_most_random_input(self):
        """На случайном наборе детектор ловит не менее 85% случаев.

        Имитируются разные способы «мазни» по клавиатуре: случайные буквы,
        буквы одного ряда, подряд идущие клавиши, повторяющиеся куски.
        """
        rng = random.Random(2024)
        rows = ['йцукенгшщзхъ', 'фывапролджэ', 'ячсмитьбю']
        alphabet = ''.join(rows)
        samples = []
        for _ in range(300):
            n = rng.randint(6, 14)
            kind = rng.randint(0, 3)
            if kind == 0:
                w = ''.join(rng.choice(alphabet) for _ in range(n))
            elif kind == 1:
                row = rng.choice(rows)
                w = ''.join(rng.choice(row) for _ in range(n))
            elif kind == 2:
                row = rng.choice(rows)
                start = rng.randrange(max(1, len(row) - n))
                w = row[start:start + n]
            else:
                chunk = ''.join(rng.choice(alphabet) for _ in range(2))
                w = (chunk * (n // 2 + 1))[:n]
            samples.append(w)

        caught = sum(1 for w in samples if looks_like_gibberish(w))
        rate = caught / len(samples)
        self.assertGreaterEqual(
            rate, 0.85,
            f'Слишком много пропусков: поймано лишь {rate:.0%}')

    def test_latin_names_not_rejected(self):
        """Латинские имена не проверяются статистикой русского языка."""
        for name in ['John', 'Smith', 'Maria', 'Anderson', 'Wolfgang']:
            with self.subTest(name=name):
                self.assertFalse(looks_like_gibberish(name))

    def test_short_words_not_analyzed(self):
        """Короткие служебные части адреса не анализируются."""
        for w in ['ул', 'д', 'кв', 'г']:
            with self.subTest(w=w):
                self.assertFalse(looks_like_gibberish(w))
