"""Проверка, что пагинация поддерживает параметр limit."""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from warehouse.models import Material


class PaginationTest(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('p', password='x')
        self.client.force_authenticate(self.user)
        for i in range(30):
            Material.objects.create(
                name=f'M{i}', unit='pc', category='other', reorder_point=1)

    def test_limit_param_works(self):
        """Параметр ?limit= должен отдавать нужное число записей."""
        r = self.client.get('/api/materials/?limit=200')
        self.assertEqual(len(r.data['results']), 30)
        self.assertEqual(r.data['count'], 30)
        self.assertIsNone(r.data['next'])

    def test_pagination_next_link(self):
        """При маленьком limit появляется ссылка next для догрузки."""
        r = self.client.get('/api/materials/?limit=10')
        self.assertEqual(len(r.data['results']), 10)
        self.assertIsNotNone(r.data['next'])
        self.assertIn('limit=10', r.data['next'])

    def test_all_pages_reachable(self):
        """Проход по всем страницам собирает все записи (логика apiCallAll)."""
        collected, url = [], '/api/materials/?limit=10'
        for _ in range(10):
            r = self.client.get(url)
            collected.extend(r.data['results'])
            if not r.data['next']:
                break
            url = r.data['next'][r.data['next'].find('/api'):]
        self.assertEqual(len(collected), 30)
