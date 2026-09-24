from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from .models import AuditJob, Environment, Snapshot
from .services import engine


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DemoTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('demo-operator', is_staff=True)
        self.client.force_login(self.user)

    def test_demo_is_offline_separate_and_renderable(self):
        original = Environment.objects.create(slug='existing', name='Existing', manager='https://existing.example')
        url = reverse('testing-data')
        self.assertContains(self.client.get(url), 'Create demo environment')
        self.assertEqual(Environment.objects.count(), 1)
        with patch.object(engine(), 'NSXClient', side_effect=AssertionError('Demo contacted NSX')):
            self.assertEqual(self.client.post(url).status_code, 302)
            snapshot = Snapshot.objects.get()
            self.assertNotEqual(snapshot.environment_id, original.pk)
            self.assertTrue(snapshot.testing)
            self.assertFalse(snapshot.environment.enabled)
            self.assertEqual(snapshot.environment.sync_interval_minutes, 0)
            self.assertEqual(AuditJob.objects.count(), 0)
            self.assertContains(self.client.get(reverse('snapshot', args=[snapshot.pk])), 'Demo web servers')
        self.assertEqual(self.client.post(f'/environments/{original.pk}/import/').status_code, 404)
        self.assertNotContains(self.client.get(reverse('environment', args=[original.pk])), 'Import JSON')

    def test_requires_staff_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('testing-data')).status_code, 403)
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.post(reverse('testing-data')).status_code, 403)
        self.assertEqual(Environment.objects.count(), 0)
