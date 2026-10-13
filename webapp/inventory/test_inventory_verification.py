from unittest.mock import Mock, patch
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from .collector import NSXClient, AuditError, needs_review
from .coverage_index import report_issues
from .comparison import compare
from .models import Environment, Snapshot, Finding
from .findings import synchronize
from .finding_recalculation import snapshots
from .finding_workflow import approval_readiness


class PaginationVerificationTests(SimpleTestCase):
    def nsx_client(self, pages):
        client = object.__new__(NSXClient)
        client.get = Mock(side_effect=pages)
        return client

    @patch('inventory.collector.time.sleep')
    def test_restart_discards_partial_attempt(self, sleep):
        client = self.nsx_client([{'results': [{'id': 'old'}], 'result_count': 2},
                              {'results': [{'id': 'new'}], 'result_count': 1}])
        self.assertEqual(list(client.items('/infra/groups')), [{'id': 'new'}])
        self.assertFalse(getattr(client, 'inventory_warnings', []))
        sleep.assert_called_once_with(2)

    @patch('inventory.collector.time.sleep')
    def test_persistent_mismatch_is_bounded_and_diagnostic(self, sleep):
        client = self.nsx_client([{'results': [{'id': 'a'}], 'result_count': 2}] * 3)
        self.assertEqual(list(client.items('/infra/groups')), [{'id': 'a'}])
        warning = client.inventory_warnings[0]
        self.assertEqual((warning['reported_total'], warning['unique_count'], warning['attempts']), (2, 1, 3))
        self.assertEqual(client.get.call_count, 3)

    @patch('inventory.collector.time.sleep')
    def test_duplicate_ids_do_not_count_as_complete(self, sleep):
        client = self.nsx_client([{'results': [{'id': 'a'}, {'id': 'a'}], 'result_count': 2}] * 3)
        self.assertEqual(list(client.items('/infra/groups')), [{'id': 'a'}])
        self.assertEqual(client.inventory_warnings[0]['duplicate_count'], 1)

    def test_duplicate_overlap_follows_cursor_until_unique_count_matches(self):
        client = self.nsx_client([
            {'results': [{'id': 'a'}, {'id': 'b'}], 'result_count': 4, 'cursor': 'next'},
            {'results': [{'id': 'b'}, {'id': 'c'}], 'cursor': 'last'},
            {'results': [{'id': 'd'}]},
        ])
        self.assertEqual(list(client.items('/search/query')), [{'id': value} for value in 'abcd'])
        self.assertEqual(client.get.call_count, 3)
        self.assertFalse(getattr(client, 'inventory_warnings', []))

    def test_search_excess_rows_explained_by_identical_duplicates(self):
        records = [{'path': '/infra/groups/{}'.format(i)} for i in range(38415)]
        client = self.nsx_client([
            {'results': records + records[-2:], 'result_count': 38415},
        ])
        self.assertEqual(list(client.items('/search/query')), records)
        self.assertFalse(getattr(client, 'inventory_warnings', []))

    @patch('inventory.collector.time.sleep')
    def test_search_excess_unique_objects_remain_unverified(self, sleep):
        records = [{'id': str(i)} for i in range(7)]
        client = self.nsx_client([{'results': records, 'result_count': 5}] * 3)
        self.assertEqual(list(client.items('/search/query')), records)
        warning = client.inventory_warnings[0]
        self.assertEqual((warning['unique_count'], warning['reported_total'],
                          warning['duplicate_count']), (7, 5, 0))

    def test_vm_overlap_uses_external_id_and_follows_cursor(self):
        records = [{'resource_type': 'VirtualMachine', 'external_id': str(i)}
                   for i in range(3)]
        client = self.nsx_client([
            {'results': records[:2], 'result_count': 3, 'cursor': 'next'},
            {'results': records[1:]},
        ])
        self.assertEqual(list(client.items('/infra/realized-state/virtual-machines')), records)
        self.assertFalse(getattr(client, 'inventory_warnings', []))

    @patch('inventory.collector.time.sleep')
    def test_vm_changed_state_across_pages_remains_unverified(self, sleep):
        vm = {'resource_type': 'VirtualMachine', 'external_id': 'vm-1'}
        client = self.nsx_client([
            {'results': [dict(vm, power_state='VM_RUNNING')],
             'result_count': 2, 'cursor': 'next'},
            {'results': [dict(vm, power_state='VM_STOPPED'),
                         {'resource_type': 'VirtualMachine', 'external_id': 'vm-2'}]},
        ] * 3)
        list(client.items('/infra/realized-state/virtual-machines'))
        self.assertIn('conflicting versions', client.inventory_warnings[0]['detail'])
        self.assertEqual(client.get.call_count, 6)

    @patch('inventory.collector.time.sleep')
    def test_conflicting_duplicate_versions_remain_unverified(self, sleep):
        client = self.nsx_client([{'results': [{'id': 'a', '_revision': 1},
                                              {'id': 'a', '_revision': 2}], 'result_count': 1}] * 3)
        list(client.items('/search/query'))
        self.assertIn('conflicting versions', client.inventory_warnings[0]['detail'])
        self.assertEqual(client.get.call_count, 3)
        self.assertEqual(client.inventory_warnings[0]['conflicts'], [
            {'resource_type': None, 'locator': 'a', 'changed_fields': ['_revision']},
        ])

    @patch('inventory.collector.time.sleep')
    def test_conflict_diagnostics_are_bounded_and_omit_values(self, sleep):
        records = [{'path': '/infra/groups/{}'.format(i), 'description': 'old-secret'}
                   for i in range(5)]
        changed = [dict(record, description='new-secret', status={}) for record in records]
        client = self.nsx_client([{'results': records + changed, 'result_count': 5}] * 3)
        list(client.items('/search/query'))
        warning = client.inventory_warnings[0]
        self.assertEqual(len(warning['conflicts']), 3)
        self.assertEqual(warning['conflicts'][0]['changed_fields'], ['description', 'status'])
        self.assertIn('/infra/groups/0', warning['detail'])
        self.assertNotIn('old-secret', str(warning))
        self.assertNotIn('new-secret', str(warning))

    @patch('inventory.collector.time.sleep')
    def test_duplicate_without_reported_total_remains_unverified(self, sleep):
        client = self.nsx_client([{'results': [{'id': 'a'}, {'id': 'a'}]}] * 3)
        list(client.items('/search/query'))
        self.assertIn('no reported total', client.inventory_warnings[0]['detail'])

    def test_shared_ids_across_types_or_parents_are_not_duplicates(self):
        records = [{'id': 'same', 'resource_type': 'Group'},
                   {'id': 'same', 'resource_type': 'Service'},
                   {'id': 'same', 'resource_type': 'Rule', 'parent_path': '/policy/a'},
                   {'id': 'same', 'resource_type': 'Rule', 'parent_path': '/policy/b'}]
        client = self.nsx_client([{'results': records, 'result_count': 4}])
        self.assertEqual(list(client.items('/search/query')), records)
        self.assertFalse(getattr(client, 'inventory_warnings', []))
        self.assertEqual(client.get.call_args.args[1]['sort_by'], 'path')

    def test_sort_rejection_retries_same_query_without_sort(self):
        client = self.nsx_client([AuditError('unsupported sort', status_code=400),
                                 {'results': [], 'result_count': 0}])
        self.assertEqual(list(client.items('/search/query', {'query': '*'})), [])
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(client.get.call_args.args[1]['query'], '*')
        self.assertNotIn('sort_by', client.get.call_args.args[1])

    def test_failed_page_and_repeated_cursor_stay_errors(self):
        for pages in ([{'results': [], 'cursor': 'x'}, AuditError('deadline reached')],
                      [{'results': [], 'cursor': 'x'}, {'results': [], 'cursor': 'x'}]):
            client = self.nsx_client(pages)
            with self.assertRaises(AuditError):
                list(client.items('/infra/groups'))
            self.assertFalse(getattr(client, 'inventory_warnings', []))


class WarningSnapshotTests(TestCase):
    def test_warning_preserves_findings_and_blocks_approval_and_replay(self):
        env = Environment.objects.create(name='Example', slug='example', manager='https://example.test')
        old = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={'objects': []})
        finding = Finding.objects.create(environment=env, snapshot=old, kind='unused', path='/a',
            name='A', qualification='eligible', present=True, first_seen=old.generated_at,
            last_seen=old.generated_at, evaluated_at=old.generated_at)
        warning = {'endpoint': '/infra/groups', 'detail': 'Count mismatch'}
        report = {'objects': [], 'inventory_warnings': [warning], 'search_coverage': {'mode': 'all_types'}}
        Snapshot.objects.create(environment=env, generated_at=timezone.now(), report=report, summary={"inventory_warnings": True}, needs_review=True)
        synchronize(env.pk)
        finding.refresh_from_db()
        self.assertTrue(finding.present)
        self.assertEqual(finding.snapshot_id, old.pk)
        self.assertEqual(approval_readiness(finding)['status'], 'blocked')
        self.assertEqual(list(snapshots(env.pk).values_list('pk', flat=True)), [old.pk])
        self.assertTrue(needs_review(report))
        self.assertEqual(list(report_issues(report))[0]['area'], 'Inventory verification')

    def test_missing_object_is_not_a_deletion(self):
        before = {'inventory': {'groups': [{'path': '/a', 'name': 'A'}]}}
        after = {'inventory': {'groups': []}, 'inventory_warnings': [{'detail': 'changed'}]}
        self.assertEqual(compare(before, after)[0]['status'], 'Presence uncertain')
