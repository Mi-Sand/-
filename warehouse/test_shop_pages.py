"""Публичные страницы магазина.

Витрина, «О производстве», «Доставка и оплата» и «Контакты» открыты без
входа в систему — покупатель не заводит учётную запись. Проверяем, что
они действительно открываются анонимному посетителю, что меню отмечает
текущий раздел и что незаполненные контакты не попадают на страницу
словом-заглушкой.
"""
from django.test import TestCase, override_settings
from django.urls import reverse

from warehouse.shop_info import FILL, company_context, is_filled

PUBLIC_PAGES = ['shop-page', 'shop-about', 'shop-delivery', 'shop-contacts']


class PublicPagesTest(TestCase):
    """Страницы доступны без входа и связаны меню."""

    def test_all_pages_open_for_anonymous(self):
        for name in PUBLIC_PAGES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(
                    response.status_code, 200,
                    f'{name} должна открываться без входа в систему')

    def test_menu_links_to_every_page(self):
        """С любой страницы можно попасть на любую другую."""
        for name in PUBLIC_PAGES:
            page = self.client.get(reverse(name))
            body = page.content.decode()
            for target in PUBLIC_PAGES:
                with self.subTest(page=name, links_to=target):
                    self.assertIn(reverse(target), body)

    def test_current_section_marked(self):
        """Текущий раздел отмечен в меню — иначе непонятно, где ты."""
        for name in PUBLIC_PAGES:
            with self.subTest(page=name):
                body = self.client.get(reverse(name)).content.decode()
                self.assertIn('class="on"', body)

    def test_contacts_page_has_route_map(self):
        """Схема проезда нарисована в самой странице.

        Сервер стоит в сети предприятия и наружу не ходит, поэтому карта
        не может подгружаться со стороны: она должна быть в разметке.
        """
        body = self.client.get(reverse('shop-contacts')).content.decode()
        self.assertIn('<svg viewBox="0 0 800 470"', body)
        self.assertIn('Дмитровское шоссе', body)

    def test_no_external_resources(self):
        """Оформление страницы не зависит от интернета.

        Шрифт, стиль, скрипт или картинка со стороннего адреса на
        изолированном сервере не загрузятся, и страница поедет. Всё, что
        отвечает за вид и работу страницы, лежит в проекте.

        Единственное исключение — встроенная карта: она сознательно
        вынесена в отдельную рамку, и её отсутствие ничего не ломает,
        см. test_map_failure_does_not_break_page.
        """
        for name in PUBLIC_PAGES:
            body = self.client.get(reverse(name)).content.decode()
            for tag in ('<script src="http', '<link href="http',
                        '<img src="http', "@import url('http"):
                with self.subTest(page=name, tag=tag):
                    self.assertNotIn(tag, body)

    def test_map_is_embedded(self):
        """Карта встроена в страницу, а не только ссылкой наружу."""
        body = self.client.get(reverse('shop-contacts')).content.decode()
        self.assertIn('<iframe', body)
        self.assertIn('map-widget', body)

    def test_map_failure_does_not_break_page(self):
        """Если карта не загрузится, покупатель не увидит пустую рамку.

        Карту рисует браузер покупателя, обращаясь к Яндексу. Там, где
        интернета нет, вместо неё должен остаться адрес и объяснение —
        поэтому подпись лежит в разметке всегда, а не подставляется
        скриптом при ошибке загрузки.
        """
        body = self.client.get(reverse('shop-contacts')).content.decode()
        self.assertIn('map-fallback', body)
        self.assertIn('Без доступа в интернет', body)
        # Схема проезда нарисована в самой странице и не зависит от карты
        self.assertIn('<svg viewBox="0 0 800 470"', body)

    def test_iframe_only_on_contacts(self):
        """Внешняя рамка есть только там, где нужна карта."""
        for name in ['shop-page', 'shop-about', 'shop-delivery']:
            with self.subTest(page=name):
                body = self.client.get(reverse(name)).content.decode()
                self.assertNotIn('<iframe', body)


class CompanyInfoTest(TestCase):
    """Сведения о предприятии подставляются во все шаблоны."""

    def test_placeholder_counts_as_empty(self):
        self.assertFalse(is_filled(FILL))
        self.assertFalse(is_filled(''))
        self.assertTrue(is_filled('+7 495 123-45-67'))

    def test_unfilled_fields_never_reach_the_page(self):
        """Слово-заглушка не должно показываться покупателю."""
        for name in PUBLIC_PAGES:
            with self.subTest(page=name):
                body = self.client.get(reverse(name)).content.decode()
                self.assertNotIn(FILL, body)

    def test_address_built_without_empty_parts(self):
        """Незаполненная часть адреса не оставляет висящую запятую."""
        data = company_context(None)['company']
        self.assertNotIn(FILL, data['address_full'])
        self.assertFalse(data['address_full'].strip().endswith(','))
        self.assertNotIn(', ,', data['address_full'])
        # Область, округ, село и дом — в таком порядке
        self.assertIn('Московская область', data['address_full'])
        self.assertIn('Синьково', data['address_full'])

    def test_address_skips_missing_district(self):
        """Без округа адрес всё равно собирается связно."""
        from warehouse import shop_info
        original = dict(shop_info.COMPANY)
        shop_info.COMPANY['district'] = FILL
        try:
            data = company_context(None)['company']
            self.assertNotIn(FILL, data['address_full'])
            self.assertNotIn(', ,', data['address_full'])
        finally:
            shop_info.COMPANY.clear()
            shop_info.COMPANY.update(original)

    def test_filled_contacts_are_shown(self):
        """Когда контакты заполнены, они попадают на страницу."""
        from warehouse import shop_info
        original = dict(shop_info.COMPANY)
        shop_info.COMPANY.update({
            'phone': '+7 495 123-45-67',
            'phone_link': '+74951234567',
            'email': 'sklad@leko.example',
            'street': 'ул. Промышленная, д. 12',
        })
        try:
            body = self.client.get(reverse('shop-contacts')).content.decode()
            self.assertIn('+7 495 123-45-67', body)
            self.assertIn('sklad@leko.example', body)
            self.assertIn('ул. Промышленная, д. 12', body)
        finally:
            shop_info.COMPANY.clear()
            shop_info.COMPANY.update(original)


@override_settings(DEBUG=False)
class PublicPagesWithoutDebugTest(TestCase):
    """Страницы не должны зависеть от отладочного режима."""

    def test_pages_open(self):
        for name in PUBLIC_PAGES:
            with self.subTest(page=name):
                self.assertEqual(
                    self.client.get(reverse(name)).status_code, 200)
