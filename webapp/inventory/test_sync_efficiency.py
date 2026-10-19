from copy import deepcopy
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from .models import Environment, Snapshot
from .previous_evidence import load
from .collector import membership_scope, retain_hit_history, statistics_backoff


class PreviousEvidenceTests(TestCase):
    def test_projection_preserves_history_and_removes_inventory(self):
        env = Environment.objects.create(name='Synthetic', slug='projection', manager='https://example.invalid')
        report = {'manager':'example.invalid','testing':False,'generated_at':'2026-10-01T00:00:00+00:00',
            'inventory': {'unused':'large'}, 'objects':[{'kind':'group','path':'/g', 'membership':'nonempty',
                'notes':['Resolved members found: virtual-machines'], 'configuration':{'unused':'large'}}],
            'dfw': {'rules':[{'path':'/r','hit_status':'traffic_recorded','hit_count':4,
                'statistics_checked_at':'2026-10-01T00:00:00+00:00','statistics':['large'],
                'policy_path':'/p','statistics_fallback_reason':'GET /p/statistics: failed'}]}}
        self.assertIsNone(load(env))
        Snapshot.objects.create(environment=env, generated_at=timezone.now(), report=report)
        lean = load(env)
        self.assertNotIn('inventory', lean)
        self.assertNotIn('statistics', lean['dfw']['rules'][0])
        self.assertNotIn('configuration', lean['objects'][0])
        left = deepcopy(report)
        right = deepcopy(report)
        retain_hit_history(left, report)
        retain_hit_history(right, lean)
        self.assertEqual(left, right)
        self.assertEqual(statistics_backoff(report, report['manager']), statistics_backoff(lean, report['manager']))


class ScopeCacheTests(SimpleTestCase):
    def test_reuse_keeps_cycles_and_nested_mac_uncertain(self):
        child = {'path':'/child','expression':[{'resource_type':'MACAddressExpression','mac_addresses':['00:00:00:00:00:01']}]}
        parent = {'path':'/parent','expression':[{'resource_type':'PathExpression','paths':['/child']}]}
        inventory = {'/child':child, '/parent':parent}
        cache = {}
        expected = membership_scope(parent, inventory)
        self.assertEqual(membership_scope(parent, inventory, cache), expected)
        self.assertIn('/child', cache)
        self.assertEqual(membership_scope(parent, inventory, cache), expected)
        cycle = {'path':'/cycle','expression':[{'resource_type':'PathExpression','paths':['/cycle']}]}
        self.assertTrue(membership_scope(cycle, {'/cycle':cycle}, {})[1])


class OverlappingRetrievalTests(SimpleTestCase):
    def test_search_firewall_and_membership_start_together(self):
        import threading
        from unittest.mock import patch, Mock
        from . import collector
        barrier = threading.Barrier(3, timeout=3)
        group = {'path':'/infra/domains/default/groups/g', 'id':'g'}
        def objects(client, path):
            return [{'path':'/infra/domains/default'}] if path == '/infra/domains' else [group] if path.endswith('/groups') else []
        def search(client, expect_resources=False):
            self.assertTrue(expect_resources)
            barrier.wait()
            return [group], {'mode':'all_types'}
        def firewall(*args, **kwargs):
            barrier.wait()
            return {'rules':[]}, []
        def membership(*args, **kwargs):
            barrier.wait()
            return 'empty', []
        class FinishedRetrieval(Exception):
            pass
        with patch.object(collector, 'objects', side_effect=objects), \
             patch.object(collector, 'search_configuration', side_effect=search), \
             patch.object(collector, 'audit_dfw', side_effect=firewall), \
             patch.object(collector, 'membership', side_effect=membership), \
             patch.object(collector, 'collect_references', side_effect=FinishedRetrieval):
            with self.assertRaises(FinishedRetrieval):
                collector.audit(Mock(), workers=4)


class CollectionReportReuseTests(TestCase):
    def test_current_in_memory_report_avoids_database_report_reload(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from .findings import synchronize
        env = Environment.objects.create(name='Reuse', slug='reuse', manager='https://example.invalid')
        snapshot = Snapshot.objects.create(environment=env, generated_at=timezone.now(),
            report={'inventory_warnings': ['incomplete']})
        with CaptureQueriesContext(connection) as queries:
            synchronize(env.pk, collected_snapshot=snapshot)
        self.assertFalse(any('SELECT' in q['sql'] and '"report"' in q['sql'] for q in queries))
        # A stale supplied object cannot override the actual latest snapshot.
        Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={'inventory_warnings':['new']})
        with CaptureQueriesContext(connection) as queries:
            synchronize(env.pk, collected_snapshot=snapshot)
        self.assertTrue(any('SELECT' in q['sql'] and '"report"' in q['sql'] for q in queries))


    def test_collection_diagnostics_shows_readable_costs_and_legacy_fallback(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from django.test import override_settings
        from .models import AuditJob
        user = get_user_model().objects.create_user('diagnostics-reviewer', is_staff=True)
        self.client.force_login(user)
        env = Environment.objects.create(name='Timings', slug='timings', manager='https://example.invalid')
        job = AuditJob.objects.create(environment=env, status='succeeded')
        snapshot = Snapshot.objects.create(environment=env, job=job, generated_at=timezone.now(), report={
            'performance': {'http': {'requests': 42}, 'phases_seconds': {'membership': 12},
                'concurrency': {'endpoints': {'membership/ip-addresses': {'successful_requests': 10, 'failed_requests': 1, 'request_seconds': 8, 'p95_seconds': 2}}}}})
        with override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}):
            response = self.client.get(reverse('collection-diagnostics', args=[job.pk]))
            self.assertContains(response, 'Where collection time was spent')
            self.assertContains(response, 'Group membership')
            self.assertContains(response, 'membership/ip-addresses')
            snapshot.report = {}; snapshot.save()
            self.assertEqual(self.client.get(reverse('collection-diagnostics', args=[job.pk])).status_code, 200)
