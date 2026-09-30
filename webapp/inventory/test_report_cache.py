from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from . import report_cache
from .models import Environment
from .services import engine, prepare_snapshot
from .tests import sample_report


@override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class ReportNavigationTests(TestCase):
    def setUp(self):
        report_cache.clear()
        self.addCleanup(report_cache.clear)
        self.user = get_user_model().objects.create_user('report-reader')
        self.client.force_login(self.user)
        self.environment = Environment.objects.create(name='Synthetic', slug='synthetic', manager='https://east.example')
        self.snapshot = self.create_snapshot()
        self.url = reverse('snapshot', args=[self.snapshot.pk])

    def create_snapshot(self):
        snapshot = prepare_snapshot(self.environment, sample_report(), imported=True)
        snapshot.save()
        return snapshot

    def test_warm_navigation_skips_json_query_and_render(self):
        with patch.object(engine(), 'render_html_report', wraps=engine().render_html_report) as render:
            cold = self.client.get(self.url)
            with CaptureQueriesContext(connection) as queries:
                warm = self.client.get(self.url)
            self.assertEqual(render.call_count, 1)
        self.assertEqual(warm.status_code, 200)
        self.assertEqual(cold.context['report'], warm.context['report'])
        self.assertFalse(any('"report"' in q['sql'] and 'inventory_snapshot' in q['sql'] for q in queries))
        self.assertContains(warm, 'href="#overview" data-primary="inventory"')
        self.assertContains(warm, 'href="#dfw-overview" data-primary="firewall"')
        self.assertEqual(warm['Cache-Control'], 'private, no-store')

    def test_cached_report_still_checks_login_existence_and_snapshot_identity(self):
        self.client.get(self.url)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.user)
        other = self.create_snapshot()
        with patch.object(engine(), 'render_html_report', wraps=engine().render_html_report) as render:
            self.assertEqual(self.client.get(reverse('snapshot', args=[other.pk])).status_code, 200)
            self.assertEqual(render.call_count, 1)
        self.snapshot.delete()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_expired_and_oversize_entries_are_not_reused(self):
        with patch.object(engine(), 'render_html_report', wraps=engine().render_html_report) as render:
            with patch.object(report_cache, '_TTL', 0):
                self.client.get(self.url)
                self.client.get(self.url)
            self.assertEqual(render.call_count, 2)
            report_cache.clear()
            with patch.object(report_cache, '_MAX_BYTES', 1):
                self.client.get(self.url)
                self.client.get(self.url)
            self.assertEqual(render.call_count, 4)
