from datetime import timedelta
from io import StringIO
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import (Environment, Snapshot, SnapshotRecord, SnapshotPresentation,
    SnapshotRelationship, SnapshotVMRule, SharedSnapshotJSON, RetentionPolicy,
    SnapshotHistoryData, SnapshotFindingEvidence, StorageMeasurement,
    SnapshotComparison, SnapshotComparisonRow, HistoryAssessment, Finding)
from .services import prepare_snapshot
from .snapshot_index import build
from .storage import share_json, clean_search_documents, storage_trend
from .storage_maintenance import cleanup_storage
from .tests import sample_report


@override_settings(STORAGES={'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class StorageLifecycleTests(TestCase):
    def setUp(self):
        self.env = Environment.objects.create(name='Storage', slug='storage', manager='https://east.example')
        self.now = timezone.now()
        self.policy = RetentionPolicy.objects.create(browsing_days=7, cache_days=3, clear_legacy_html=True)
        self.old = prepare_snapshot(self.env, sample_report())
        self.old.save()
        build(self.old)
        Snapshot.objects.filter(pk=self.old.pk).update(created_at=self.now-timedelta(days=20), generated_at=self.now-timedelta(days=20), browsing_refreshed_at=self.now-timedelta(days=20), html='obsolete')
        self.latest = prepare_snapshot(self.env, sample_report())
        self.latest.generated_at = self.now
        self.latest.save()
        build(self.latest)

    def test_archive_preserves_sources_and_observations_and_rebuilds(self):
        source = self.old.report
        SnapshotFindingEvidence.objects.create(snapshot=self.old, kind='empty_group', path='/g', name='G', fingerprint='same')
        cleanup_storage(self.now)
        self.old.refresh_from_db()
        self.assertEqual(self.old.browsing_status, 'archived')
        self.assertEqual(self.old.report, source)
        self.assertEqual(self.old.html, '')
        self.assertTrue(SnapshotFindingEvidence.objects.filter(snapshot=self.old).exists())
        self.assertTrue(SnapshotHistoryData.objects.filter(snapshot=self.old).exists())
        self.assertFalse(SnapshotRecord.objects.filter(snapshot=self.old).exists())
        self.assertTrue(SnapshotPresentation.objects.filter(snapshot=self.latest).exists())
        admin = get_user_model().objects.create_superuser('storage-admin', password='test')
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse('snapshot', args=[self.old.pk])), 'Rebuild detailed view')
        self.assertEqual(self.client.get(reverse('snapshot-data', args=[self.old.pk])).status_code, 409)
        self.assertEqual(self.client.get(reverse('restore-browsing', args=[self.old.pk])).status_code, 405)
        self.client.post(reverse('restore-browsing', args=[self.old.pk]))
        self.old.refresh_from_db()
        self.assertEqual(self.old.browsing_status, 'queued')
        call_command('index_snapshots', snapshot=self.old.pk, stdout=StringIO())
        self.old.refresh_from_db()
        self.assertEqual(self.old.browsing_status, 'ready')
        self.assertTrue(SnapshotRecord.objects.filter(snapshot=self.old).exists())
        cleanup_storage(self.now)
        self.assertTrue(SnapshotPresentation.objects.filter(snapshot=self.old).exists())

    def test_archiving_protects_current_findings(self):
        Finding.objects.create(environment=self.env, snapshot=self.old, kind='empty_group', path='/g', name='G', fingerprint='x', first_seen=self.now, last_seen=self.now, evaluated_at=self.now)
        cleanup_storage(self.now)
        self.assertTrue(SnapshotPresentation.objects.filter(snapshot=self.old).exists())

    def test_disabled_cleanup_and_restore_permissions(self):
        self.policy.enabled = False
        self.policy.save()
        cleanup_storage(self.now)
        self.assertTrue(SnapshotPresentation.objects.filter(snapshot=self.old).exists())
        user = get_user_model().objects.create_user('reader')
        self.client.force_login(user)
        self.assertEqual(self.client.post(reverse('restore-browsing', args=[self.old.pk])).status_code, 403)

    def test_cache_expiry_keeps_pair_and_active_work(self):
        pair = SnapshotComparison.objects.create(before=self.old, after=self.latest, ready=True, status='completed', cache_accessed_at=self.now-timedelta(days=10))
        SnapshotComparisonRow.objects.create(comparison=pair, ordinal=0, data={'name':'change'})
        assessment = HistoryAssessment.objects.create(environment=self.env, cache_key='old', expires_at=self.now-timedelta(days=10))
        cleanup_storage(self.now)
        pair.refresh_from_db()
        self.assertEqual(pair.status, 'expired')
        self.assertFalse(pair.rows.exists())
        self.assertFalse(HistoryAssessment.objects.filter(pk=assessment.pk).exists())
        pair.status = 'running'
        pair.save()
        cleanup_storage(self.now)
        pair.refresh_from_db()
        self.assertEqual(pair.status, 'running')

    def test_large_relationship_and_rule_payloads_share_and_read_exactly(self):
        from .tag_relationships import read
        from .vm_relationships import ExpandedVMData
        record = SnapshotRecord.objects.create(snapshot=self.latest, ordinal=999, view='tags', name='tag', sort_name='tag', compact={}, data={}, columns=[], sort_values={}, search_basic='', search_evidence='', relationships_ready=True)
        data = {'path':'/rule', 'name':'large'*150, 'via_group':'/g'}
        rows = [SnapshotRelationship(record=record, section='firewall_references', position=i, data=data) for i in range(2)]
        share_json(rows)
        SnapshotRelationship.objects.bulk_create(rows)
        rule = SnapshotVMRule(snapshot=self.latest, path='/rule', data=data)
        share_json([rule]); rule.save()
        self.assertEqual(rows[0].payload_id, rule.payload_id)
        self.assertEqual(read(self.latest.pk, 999, 'firewall_references')['items'], [data, data])
        vm = SnapshotRecord.objects.create(snapshot=self.latest, ordinal=1000, view='vms', name='vm', sort_name='vm', compact={}, data={'_shared_vm_rules':True, 'related_rules':[{'path':'/rule'}]}, columns=[], sort_values={}, search_basic='', search_evidence='')
        expanded = SnapshotRecord.objects.filter(pk=vm.pk).annotate(value=ExpandedVMData()).values_list('value', flat=True).get()
        self.assertEqual(expanded['related_rules'][0], data)
        self.assertEqual(clean_search_documents(), 0)

    def test_storage_alert_forecast_and_stale_measurements(self):
        today = timezone.localdate()
        self.policy.database_budget_gib = 100
        self.policy.save()
        StorageMeasurement.objects.create(day=today-timedelta(days=7), database_bytes=50*1024**3)
        StorageMeasurement.objects.create(day=today, database_bytes=70*1024**3)
        self.assertIn('may reach', storage_trend()['alert'])
        StorageMeasurement.objects.filter(day=today).update(database_bytes=85*1024**3)
        self.assertIn('85.0%', storage_trend()['alert'])
        StorageMeasurement.objects.filter(day=today).delete()
        self.assertTrue(storage_trend()['stale'])
        self.assertIsNone(storage_trend()['forecast'])

    def test_restore_worker_failure_is_visible_and_retryable(self):
        from unittest.mock import patch
        from .management.commands.audit_worker import Command
        Snapshot.objects.filter(pk=self.old.pk).update(browsing_status='queued')
        with patch('inventory.management.commands.audit_worker.subprocess.Popen', side_effect=OSError('test failure')):
            self.assertTrue(Command().run_browsing_restore())
        self.old.refresh_from_db()
        self.assertEqual(self.old.browsing_status, 'failed')

    def test_relationship_compaction_preserves_payload_and_is_resumable(self):
        row = SnapshotVMRule.objects.create(snapshot=self.old, path='/large', data={'text':'x'*500})
        out = StringIO()
        call_command('compact_relationship_storage', kind='vm-rules', stdout=out)
        row.refresh_from_db()
        self.assertIsNone(row.payload_id)
        call_command('compact_relationship_storage', kind='vm-rules', apply=True, stdout=out)
        row.refresh_from_db()
        self.assertEqual(row.payload.data, {'text':'x'*500})
        self.assertEqual(row.data, {})
        call_command('compact_relationship_storage', kind='vm-rules', apply=True, stdout=out)
        self.assertEqual(SharedSnapshotJSON.objects.filter(data={'text':'x'*500}).count(), 1)

    def test_admin_storage_notification_and_policy_validation(self):
        admin = get_user_model().objects.create_superuser('budget-admin', password='test')
        self.client.force_login(admin)
        self.policy.database_budget_gib = 1
        self.policy.save()
        StorageMeasurement.objects.create(day=timezone.localdate(), database_bytes=2*1024**3)
        response = self.client.get(reverse('notifications'))
        self.assertTrue(any('configured budget' in row['label'] for row in response.json()['items']))
        from .forms import RetentionForm
        form = RetentionForm(data={'enabled':'on', 'snapshot_days':180, 'testing_days':7, 'collection_days':180,
            'storage_warning_percent':101, 'browsing_days':-1}, instance=self.policy)
        self.assertFalse(form.is_valid())
        self.assertIn('storage_warning_percent', form.errors)
        self.assertIn('browsing_days', form.errors)

    def test_opening_expired_comparison_queues_reconstruction(self):
        pair = SnapshotComparison.objects.create(before=self.old, after=self.latest, status='expired', ready=False)
        user = get_user_model().objects.create_user('comparison-reader')
        self.client.force_login(user)
        response = self.client.get(reverse('snapshot-comparison', args=[self.env.pk]), {'before':str(self.old.pk), 'after':str(self.latest.pk)})
        self.assertEqual(response.status_code, 200)
        pair.refresh_from_db()
        self.assertEqual(pair.status, 'queued')
        self.assertGreaterEqual(pair.cache_accessed_at, self.now)
