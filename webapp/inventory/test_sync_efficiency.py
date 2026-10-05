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
        def search(client):
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
