from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, SnapshotComparison, FindingRecalculation, AuditEvent
from .comparison_cache import comparison_rows


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class TaskCancellationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('task-admin', password='test-only')
        self.client.force_login(self.user)
        self.env = Environment.objects.create(name='Tasks', slug='tasks', manager='https://example.test')
        self.before = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report={})
        self.after = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report={})
        self.pair = SnapshotComparison.objects.create(before=self.before, after=self.after)

    def cancel(self, kind, pk):
        return self.client.post(reverse('task-cancel', args=[kind, pk]), {'confirm':'yes', 'reason':'No longer needed'})

    def test_queued_comparison_cancel_and_retry(self):
        self.cancel('comparison', self.pair.pk)
        self.pair.refresh_from_db(); self.assertEqual(self.pair.status, 'cancelled')
        with patch('inventory.comparison_cache.configuration') as load:
            list(comparison_rows(self.before, self.after)); load.assert_not_called()
        self.client.post(reverse('comparison-retry', args=[self.pair.pk]))
        self.pair.refresh_from_db(); self.assertEqual(self.pair.status, 'queued')
        self.assertTrue(AuditEvent.objects.filter(action='comparison.cancel_requested').exists())

    def test_running_comparison_discards_result_after_cancel(self):
        self.pair.status = 'running'; self.pair.save()
        def compare(*args):
            self.cancel('comparison', self.pair.pk)
            return [{'path':'/example'}]
        with patch('inventory.comparison_cache.configuration', return_value={}), patch('inventory.comparison_cache.compare', side_effect=compare):
            list(comparison_rows(self.before, self.after))
        self.pair.refresh_from_db()
        self.assertEqual(self.pair.status, 'cancelled')
        self.assertFalse(self.pair.ready)
        self.assertFalse(self.pair.rows.exists())

    def test_completed_comparison_not_cancelled(self):
        self.pair.status = 'completed'; self.pair.ready = True; self.pair.save()
        self.cancel('comparison', self.pair.pk)
        self.pair.refresh_from_db(); self.assertEqual(self.pair.status, 'completed')

    def test_recalculation_cancellation_prevents_publication(self):
        from . import finding_recalculation as recalc
        job = FindingRecalculation.objects.create(environment=self.env, status='running', kinds=['unused'])
        def stop(*args):
            self.cancel('recalculation', job.pk)
        with patch.object(recalc, 'index', side_effect=stop):
            recalc.run(job.pk, job.token)
        job.refresh_from_db()
        self.assertEqual(job.status, 'cancelling')
        self.assertFalse(self.env.findings.exists())

    def test_queued_recalculation_cancelled_immediately(self):
        job = FindingRecalculation.objects.create(environment=self.env)
        self.cancel('recalculation', job.pk)
        job.refresh_from_db(); self.assertEqual(job.status, 'cancelled')
        self.assertIsNotNone(job.finished_at)

    def test_regular_viewer_cannot_cancel_shared_task(self):
        viewer = get_user_model().objects.create_user('viewer')
        self.client.force_login(viewer)
        self.assertEqual(self.cancel('comparison', self.pair.pk).status_code, 403)
        self.pair.refresh_from_db(); self.assertEqual(self.pair.status, 'queued')

    def test_restart_reconciliation_only_acknowledges_owned_lane(self):
        from .task_cancellation import reconcile
        from .models import DeletionBatch
        job = FindingRecalculation.objects.create(environment=self.env, status='cancelling')
        self.pair.status = 'cancelling'; self.pair.save()
        batch = DeletionBatch.objects.create(environment=self.env, manager=self.env.manager, status='cancelling')
        reconcile('comparison')
        self.pair.refresh_from_db(); job.refresh_from_db(); batch.refresh_from_db()
        self.assertEqual(self.pair.status, 'cancelled')
        self.assertEqual(job.status, 'cancelling')
        self.assertEqual(batch.status, 'cancelling')
        reconcile('recalculation'); reconcile('deletion_preview')
        job.refresh_from_db(); batch.refresh_from_db()
        self.assertEqual(job.status, 'cancelled'); self.assertEqual(batch.status, 'cancelled')
