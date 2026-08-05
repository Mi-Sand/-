"""Тесты уведомлений чата: подсчёт непрочитанных и отметки прочтения."""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import ChatMessage, ChatReadState

User = get_user_model()


class ChatUnreadTest(APITestCase):
    def setUp(self):
        self.anna = User.objects.create_user(
            'anna', password='x', first_name='Анна', last_name='Петрова')
        self.boris = User.objects.create_user(
            'boris', password='x', first_name='Борис')
        self.vera = User.objects.create_user('vera', password='x')

    def _as(self, user):
        self.client.force_authenticate(user)
        return self.client

    def _say(self, sender, text, to=None):
        return ChatMessage.objects.create(
            sender=sender, recipient=to, text=text)

    def test_no_unread_initially(self):
        r = self._as(self.boris).get('/api/chat/unread/')
        self.assertEqual(r.data['total'], 0)

    def test_counts_general_and_private_separately(self):
        self._say(self.anna, 'общее 1')
        self._say(self.anna, 'общее 2')
        self._say(self.anna, 'личное', to=self.boris)

        r = self._as(self.boris).get('/api/chat/unread/')
        self.assertEqual(r.data['total'], 3)
        self.assertEqual(r.data['general'], 2)
        self.assertEqual(r.data['private'][str(self.anna.id)], 1)

    def test_own_messages_are_not_unread(self):
        """Свои же сообщения не должны считаться непрочитанными."""
        self._say(self.anna, 'общее')
        r = self._as(self.anna).get('/api/chat/unread/')
        self.assertEqual(r.data['total'], 0)

    def test_private_message_invisible_to_third_party(self):
        """Чужая переписка не попадает в счётчик постороннего."""
        self._say(self.anna, 'секрет', to=self.boris)
        r = self._as(self.vera).get('/api/chat/unread/')
        self.assertEqual(r.data['total'], 0)

    def test_mark_private_read(self):
        self._say(self.anna, 'личное 1', to=self.boris)
        self._say(self.anna, 'личное 2', to=self.boris)
        self._say(self.anna, 'общее')

        client = self._as(self.boris)
        client.post('/api/chat/read/', {'with': self.anna.id}, format='json')

        r = client.get('/api/chat/unread/')
        self.assertEqual(r.data['private'], {})
        self.assertEqual(r.data['general'], 1)   # общий чат не тронут

    def test_mark_general_read(self):
        self._say(self.anna, 'общее 1')
        self._say(self.anna, 'общее 2')

        client = self._as(self.boris)
        client.post('/api/chat/read/', {}, format='json')

        self.assertEqual(client.get('/api/chat/unread/').data['total'], 0)

    def test_new_message_after_read_counts_again(self):
        self._say(self.anna, 'первое')
        client = self._as(self.boris)
        client.post('/api/chat/read/', {}, format='json')

        self._say(self.anna, 'второе')
        self.assertEqual(client.get('/api/chat/unread/').data['total'], 1)

    def test_repeated_mark_is_safe(self):
        """Повторная отметка не создаёт дублей и не ломает счётчик."""
        self._say(self.anna, 'общее')
        client = self._as(self.boris)
        for _ in range(3):
            client.post('/api/chat/read/', {}, format='json')

        self.assertEqual(client.get('/api/chat/unread/').data['total'], 0)
        self.assertEqual(
            ChatReadState.objects.filter(user=self.boris, peer=None).count(), 1)

    def test_latest_describes_newest_message(self):
        """Для уведомления возвращается последнее непрочитанное."""
        self._say(self.anna, 'старое')
        self._say(self.anna, 'самое свежее', to=self.boris)

        latest = self._as(self.boris).get('/api/chat/unread/').data['latest']
        self.assertEqual(latest['text'], 'самое свежее')
        self.assertEqual(latest['sender_id'], self.anna.id)
        self.assertTrue(latest['is_private'])
        self.assertIn('Анна', latest['sender_name'])

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get('/api/chat/unread/').status_code, (401, 403))
        self.assertIn(self.client.post('/api/chat/read/', {}, format='json')
                      .status_code, (401, 403))
