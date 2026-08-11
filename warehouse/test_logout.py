"""Выход из системы.

Кнопка выхода однажды уже перестала работать молча: Django с пятой
версии отвечает на переход по ссылке «метод не разрешён», а в разметке
стояла обычная ссылка. Внешне ничего не менялось — нажатие просто не
делало ничего, и в журнале копились строки «GET /logout/ 405». Человек
при этом остаётся в системе, хотя уверен, что вышел, — на общем
компьютере это уже не мелочь.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class LogoutTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='klad', password='x')
        self.client.force_login(self.user)

    def logged_in(self):
        return '_auth_user_id' in self.client.session

    def test_logout_works(self):
        answer = self.client.post(reverse('logout'))
        self.assertEqual(answer.status_code, 302)
        self.assertFalse(self.logged_in())

    def test_logout_leads_to_login_page(self):
        answer = self.client.post(reverse('logout'), follow=True)
        self.assertContains(answer, 'Вход')

    def test_page_has_a_working_exit(self):
        """В шапке должна быть форма выхода, а не ссылка.

        Проверяется именно разметка: ссылка выглядит так же, работает
        так же на вид и не работает на деле.
        """
        page = self.client.get('/')
        self.assertContains(page, f'action="{reverse("logout")}"')
        self.assertContains(page, 'csrfmiddlewaretoken')

    def test_stranger_is_not_logged_out_by_a_link(self):
        """Выход по чужой ссылке не проходит.

        Иначе достаточно подсунуть сотруднику картинку с этим адресом,
        чтобы выбрасывать его из системы посреди работы.
        """
        answer = self.client.get(reverse('logout'))
        self.assertEqual(answer.status_code, 405)
        self.assertTrue(self.logged_in())
