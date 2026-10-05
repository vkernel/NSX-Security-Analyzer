from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from .system_health import resources, database_usage


class ResourceTests(SimpleTestCase):
    def test_cgroup_v2_and_unlimited_memory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'memory.current').write_text('1024')
            (root / 'memory.max').write_text('2048')
            (root / 'cpu.stat').write_text('usage_usec 1000000\nnr_periods 2\n')
            result = resources(directory, directory)
            self.assertEqual(result['memory_percent'], 50)
            self.assertEqual(result['cpu_cores'], 0)
            (root / 'memory.max').write_text('max')
            self.assertIsNone(resources(directory, directory)['memory_limit'])

    def test_missing_metrics_are_not_reported_as_zero(self):
        with TemporaryDirectory() as directory:
            result = resources(directory, directory + '/missing')
        self.assertIsNone(result['memory'])
        self.assertIsNone(result['cpu_cores'])
        self.assertIsNone(result['disk'])

    def test_legacy_cgroup(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'memory').mkdir()
            (root / 'memory/memory.usage_in_bytes').write_text('2048')
            (root / 'memory/memory.limit_in_bytes').write_text(str(1 << 62))
            result = resources(directory, directory)
        self.assertEqual(result['memory'], 2048)
        self.assertIsNone(result['memory_limit'])


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class HealthPageTests(TestCase):
    def test_requires_admin_and_does_not_probe_for_viewer(self):
        url = reverse('system-health')
        self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user('health-operator', is_staff=True)
        self.client.force_login(user)
        with patch('inventory.system_health.resources') as probe:
            self.assertEqual(self.client.get(url).status_code, 403)
            probe.assert_not_called()

    def test_admin_page_and_database_measurements(self):
        user = get_user_model().objects.create_superuser('health-admin', password='test-only')
        self.client.force_login(user)
        response = self.client.get(reverse('system-health'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'System health')
        self.assertContains(response, 'not represent remote database storage')
        self.assertIn('no-store', response['Cache-Control'])
        result = database_usage()
        self.assertGreater(result['size'], 0)
        self.assertLessEqual(len(result['tables']), 10)

    def test_database_failure_is_redacted(self):
        with patch('inventory.system_health.connection.cursor', side_effect=DatabaseError('secret-host')):
            result = database_usage()
        self.assertIn('unavailable', result['error'])
        self.assertNotIn('secret-host', str(result))
