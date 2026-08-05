"""Тесты сводки: данные графика и формат дат."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from warehouse.models import (Material, Stock, StockMovement, Warehouse)

User = get_user_model()


class DashboardChartTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('t', password='x', role='admin')
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(name='Склад', type='raw')
        self.mat = Material.objects.create(
            name='Кожа', unit='m', category='leather', reorder_point=10)
        Stock.objects.create(warehouse=self.wh, material=self.mat, quantity=0)

    def _move(self, kind, qty, days_ago):
        mv = StockMovement.objects.create(
            warehouse=self.wh, material=self.mat,
            movement_type=kind, quantity=qty, user=self.user)
        StockMovement.objects.filter(id=mv.id).update(
            created_at=timezone.now() - timedelta(days=days_ago))
        return mv

    def test_chart_always_covers_two_weeks(self):
        """График всегда содержит 14 дней, даже без операций."""
        chart = self.client.get('/api/dashboard/').data['chart']
        self.assertEqual(len(chart), 14)
        for day in chart:
            self.assertIn('date', day)
            self.assertIn('in', day)
            self.assertIn('out', day)

    def test_empty_chart_is_all_zero(self):
        chart = self.client.get('/api/dashboard/').data['chart']
        self.assertTrue(all(d['in'] == 0 and d['out'] == 0 for d in chart))

    def test_movements_land_on_correct_days(self):
        """Приход и расход попадают в свои дни и не смешиваются."""
        self._move('in', 100, days_ago=3)
        self._move('out', 40, days_ago=3)
        self._move('in', 25, days_ago=1)

        chart = self.client.get('/api/dashboard/').data['chart']
        by_date = {d['date']: d for d in chart}

        three = (timezone.localdate() - timedelta(days=3)).strftime('%d.%m')
        one = (timezone.localdate() - timedelta(days=1)).strftime('%d.%m')

        self.assertEqual(by_date[three]['in'], 100)
        self.assertEqual(by_date[three]['out'], 40)
        self.assertEqual(by_date[one]['in'], 25)
        self.assertEqual(by_date[one]['out'], 0)

    def test_same_day_movements_are_summed(self):
        """Несколько операций за день складываются."""
        self._move('in', 10, days_ago=2)
        self._move('in', 15, days_ago=2)

        chart = self.client.get('/api/dashboard/').data['chart']
        day = (timezone.localdate() - timedelta(days=2)).strftime('%d.%m')
        self.assertEqual({d['date']: d for d in chart}[day]['in'], 25)

    def test_older_movements_are_excluded(self):
        """Операции старше двух недель в график не попадают."""
        self._move('in', 999, days_ago=30)
        chart = self.client.get('/api/dashboard/').data['chart']
        self.assertTrue(all(d['in'] == 0 for d in chart))

    def test_movement_date_is_human_readable(self):
        """В журнале дата выводится в привычном виде, а не в формате базы."""
        self._move('in', 5, days_ago=0)
        moves = self.client.get('/api/dashboard/').data['recent_movements']
        shown = moves[0]['created_display']

        self.assertRegex(shown, r'^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$')
        self.assertNotIn('T', shown)      # без ISO-разделителя
        self.assertNotIn('+', shown)      # без часового пояса


class DashboardPeriodTest(APITestCase):
    """Выбор периода графика."""

    def setUp(self):
        self.user = User.objects.create_user('p', password='x', role='admin')
        self.client.force_authenticate(self.user)

    def test_default_period_is_two_weeks(self):
        self.assertEqual(len(self.client.get('/api/dashboard/').data['chart']), 14)

    def test_period_can_be_changed(self):
        for days in (7, 30, 90):
            with self.subTest(days=days):
                data = self.client.get(f'/api/dashboard/?days={days}').data
                self.assertEqual(len(data['chart']), days)

    def test_period_is_clamped_to_sane_range(self):
        """Слишком малые и слишком большие значения приводятся к границам."""
        self.assertEqual(len(self.client.get('/api/dashboard/?days=1').data['chart']), 7)
        self.assertEqual(len(self.client.get('/api/dashboard/?days=9999').data['chart']), 90)

    def test_broken_period_falls_back_to_default(self):
        """Некорректный параметр не должен ломать страницу."""
        for bad in ('abc', '', 'null', '1.5'):
            with self.subTest(value=bad):
                data = self.client.get(f'/api/dashboard/?days={bad}').data
                self.assertEqual(len(data['chart']), 14)

    def test_chart_points_have_full_date(self):
        """Для подсказки нужна полная дата с годом."""
        point = self.client.get('/api/dashboard/').data['chart'][0]
        self.assertRegex(point['full_date'], r'^\d{2}\.\d{2}\.\d{4}$')
        self.assertRegex(point['date'], r'^\d{2}\.\d{2}$')
