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
