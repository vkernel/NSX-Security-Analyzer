from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, AuditJob, RetentionPolicy
from .retention import cleanup_retention, preview
from .tests import sample_report


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RetentionTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.environment = Environment.objects.create(name='East', slug='east', manager='https://east.example')
        self.policy = RetentionPolicy.objects.create(enabled=True, snapshot_days=91, testing_days=7, collection_days=180)
        self.admin = get_user_model().objects.create_user('retention-admin', is_staff=True, is_superuser=True)

    def snapshot(self, days, testing=False, imported=False):
        stamp = self.now - timedelta(days=days)
        job = AuditJob.objects.create(environment=self.environment, status='succeeded', finished_at=stamp)
        AuditJob.objects.filter(pk=job.pk).update(created_at=stamp)
        snapshot = Snapshot.objects.create(environment=self.environment, job=job, testing=testing,
            generated_at=stamp, report=sample_report(), imported=imported)
        if not imported:
            Snapshot.objects.filter(pk=snapshot.pk).update(created_at=stamp)
        return snapshot

    def test_disabled_does_not_delete(self):
        old = self.snapshot(400)
        self.snapshot(0)
        self.policy.enabled = False
        self.policy.save()
        self.assertEqual(cleanup_retention(self.now), (0, 0))
        self.assertTrue(Snapshot.objects.filter(pk=old.pk).exists())

    def test_preview_is_read_only_and_cleanup_keeps_latest_and_baseline(self):
        old = self.snapshot(400)
        baseline = self.snapshot(90.9)
        latest = self.snapshot(0)
        recent_import = self.snapshot(500, imported=True)
        old_test = self.snapshot(100, testing=True)
        latest_test = self.snapshot(20, testing=True)
        rows = preview(self.policy, self.now)
        self.assertEqual(rows[0]['snapshots'], 2)
        self.assertEqual(Snapshot.objects.count(), 6)
        self.assertEqual(cleanup_retention(self.now), (2, 1))
        self.assertFalse(Snapshot.objects.filter(pk__in=[old.pk, old_test.pk]).exists())
        self.assertEqual(set(Snapshot.objects.values_list('pk', flat=True)), {baseline.pk, latest.pk, recent_import.pk, latest_test.pk})
        self.assertTrue(AuditJob.objects.filter(pk=old_test.job_id).exists())
        self.assertEqual(cleanup_retention(self.now), (0, 0))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('collection-history', args=[self.environment.pk])), 'Snapshot removed by retention')

    def test_ancient_latest_full_snapshot_and_its_job_are_preserved(self):
        latest = self.snapshot(500)
        self.assertEqual(cleanup_retention(self.now), (0, 0))
        self.assertTrue(Snapshot.objects.filter(pk=latest.pk).exists())
        self.assertTrue(AuditJob.objects.filter(pk=latest.job_id).exists())

    def test_active_environment_is_skipped(self):
        self.snapshot(400)
        self.snapshot(0)
        AuditJob.objects.create(environment=self.environment, status='running')
        self.assertTrue(preview(self.policy, self.now)[0]['busy'])
        self.assertEqual(cleanup_retention(self.now), (0, 0))
        self.assertEqual(Snapshot.objects.count(), 2)

    def test_keep_forever_and_unsafe_direct_policy(self):
        self.snapshot(400)
        self.snapshot(0)
        self.policy.snapshot_days = self.policy.testing_days = self.policy.collection_days = 0
        self.policy.save()
        self.assertEqual(cleanup_retention(self.now), (0, 0))
        self.policy.snapshot_days = 7
        self.policy.last_run = None
        self.policy.save()
        self.assertEqual(cleanup_retention(self.now), (0, 0))
        self.assertEqual(Snapshot.objects.count(), 2)

    def test_permissions_preview_and_explicit_save(self):
        viewer = get_user_model().objects.create_user('operator', is_staff=True)
        url = reverse('retention-settings')
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        data = {'enabled': '', 'snapshot_days': 180, 'testing_days': 30, 'collection_days': 180, 'action': 'preview'}
        self.assertContains(self.client.post(url, data), 'not saved')
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.snapshot_days, 91)
        data['action'] = 'save'
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.policy.refresh_from_db()
        self.assertFalse(self.policy.enabled)
        self.assertEqual(self.policy.snapshot_days, 180)
        data['snapshot_days'] = 7
        self.assertEqual(self.client.post(url, data).status_code, 200)
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.snapshot_days, 180)

    def test_failed_collection_expiry_is_independent_of_snapshots(self):
        job = AuditJob.objects.create(environment=self.environment, status='failed', finished_at=self.now-timedelta(days=200))
        AuditJob.objects.filter(pk=job.pk).update(created_at=self.now-timedelta(days=201))
        self.assertEqual(cleanup_retention(self.now), (0, 1))
