"""Collection reliability: slots, orphaned runs, automatic retry, search gaps, phase overlap."""
import re
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import urllib3
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import collector
from .collector import AuditError
from .evidence_coverage import issues, relevant
from .models import AuditJob, Environment, Finding, FindingPolicy, Snapshot
from .request_pacing import CollectionDeadlineExceeded
from .services import (claim_job, enqueue, failure_code, reconcile_orphaned_collections, schedule_retry)
from .worker_lanes import LANES, active_lanes


class LockIdentityTests(SimpleTestCase):
    def test_every_advisory_lock_id_is_unique(self):
        lanes = set(LANES.values())
        self.assertEqual(len(lanes), len(LANES))
        others = set()
        for path in Path(__file__).parent.glob('*.py'):
            if path.name.startswith('test') or path.name == 'worker_lanes.py':
                continue
            others.update(int(value) for value in re.findall(r'\b78124\d{4}\b', path.read_text(errors='ignore')))
        self.assertFalse(lanes & others, 'A worker lane lease shares an advisory lock with another lock.')

    def test_supervisor_starts_only_configured_collection_slots(self):
        with override_settings(COLLECTION_SLOTS=1):
            self.assertEqual([lane for lane in active_lanes() if lane.startswith('collection')], ['collection'])
        with override_settings(COLLECTION_SLOTS=3):
            self.assertEqual([lane for lane in active_lanes() if lane.startswith('collection')],
                             ['collection', 'collection_2', 'collection_3'])


class FailureClassificationTests(SimpleTestCase):
    def wrapped(self, cause, status=None):
        try:
            try:
                raise cause
            except Exception as exc:
                raise AuditError('GET /infra failed', status_code=status) from exc
        except AuditError as exc:
            return exc

    def test_temporary_conditions_retry_and_permanent_ones_do_not(self):
        self.assertEqual(failure_code(AuditError('busy', status_code=503)), 'NSX_UNAVAILABLE')
        self.assertEqual(failure_code(AuditError('throttled', status_code=429)), 'NSX_UNAVAILABLE')
        self.assertEqual(failure_code(AuditError('denied', status_code=401)), 'NSX_ACCESS_DENIED')
        self.assertEqual(failure_code(self.wrapped(urllib3.exceptions.NewConnectionError(None, 'refused'))), 'NETWORK_ERROR')
        self.assertEqual(failure_code(self.wrapped(urllib3.exceptions.ReadTimeoutError(None, '/', 'slow'))), 'NETWORK_TIMEOUT')
        self.assertEqual(failure_code(self.wrapped(urllib3.exceptions.SSLError('certificate verify failed'))), 'TLS_ERROR')
        self.assertEqual(failure_code(CollectionDeadlineExceeded('deadline')), 'COLLECTION_DEADLINE')
        try:
            try:
                raise CollectionDeadlineExceeded('deadline')
            except CollectionDeadlineExceeded as inner:
                raise TimeoutError('phase stopped') from inner
        except TimeoutError as outer:
            self.assertEqual(failure_code(outer), 'COLLECTION_DEADLINE', 'A deadline anywhere in the chain never retries.')
        self.assertIsNone(failure_code(AuditError('Search results are missing 3 inventory objects')))


@override_settings(COLLECTION_SLOTS=2, COLLECTION_RETRY_SECONDS=900)
class SlotAndRetryTests(TestCase):
    def setUp(self):
        self.a = Environment.objects.create(name='A', slug='slot-a', manager='https://a.example.test')
        self.b = Environment.objects.create(name='B', slug='slot-b', manager='https://b.example.test')
        self.c = Environment.objects.create(name='C', slug='slot-c', manager='https://c.example.test')

    def test_slots_run_different_environments_in_parallel(self):
        for environment in (self.a, self.b, self.c):
            AuditJob.objects.create(environment=environment)
        first, second = claim_job('collection'), claim_job('collection_2')
        self.assertEqual({first.environment_id, second.environment_id}, {self.a.pk, self.b.pk})
        self.assertEqual((first.worker_slot, second.worker_slot), ('collection', 'collection_2'))
        self.assertIsNone(claim_job('collection_3'), 'At most COLLECTION_SLOTS collections run at once.')

    def test_delayed_retry_is_not_claimed_early(self):
        AuditJob.objects.create(environment=self.a, not_before=timezone.now() + timedelta(minutes=5))
        self.assertIsNone(claim_job())
        AuditJob.objects.filter(environment=self.a).update(not_before=timezone.now() - timedelta(seconds=1))
        self.assertIsNotNone(claim_job())

    def test_orphaned_run_of_this_slot_fails_and_queues_one_retry(self):
        mine = AuditJob.objects.create(environment=self.a, status='running', worker_slot='collection_2', started_at=timezone.now())
        legacy = AuditJob.objects.create(environment=self.b, status='running', worker_slot='', started_at=timezone.now())
        other = AuditJob.objects.create(environment=self.c, status='running', worker_slot='collection', started_at=timezone.now())
        self.assertEqual(reconcile_orphaned_collections('collection_2'), 1)
        mine.refresh_from_db(); other.refresh_from_db(); legacy.refresh_from_db()
        self.assertEqual(mine.status, 'failed')
        self.assertEqual(mine.diagnostics['error']['code'], 'WORKER_ORPHANED')
        self.assertEqual((other.status, legacy.status), ('running', 'running'))
        retry = mine.retries.get()
        self.assertEqual(retry.status, 'queued')
        self.assertGreater(retry.not_before, timezone.now() + timedelta(minutes=14))
        # The default slot also owns rows from workers that predate slots.
        self.assertEqual(reconcile_orphaned_collections('collection'), 2)

    def test_default_slot_reconciles_slots_no_longer_configured(self):
        stranded = AuditJob.objects.create(environment=self.a, status='running', worker_slot='collection_3', started_at=timezone.now())
        self.assertEqual(reconcile_orphaned_collections('collection'), 1, 'Only two slots are configured; nothing runs slot 3.')
        stranded.refresh_from_db()
        self.assertEqual(stranded.status, 'failed')

    def test_waiting_retry_does_not_block_live_membership_checks(self):
        from .group_membership_paging import _gate
        failed = AuditJob.objects.create(environment=self.a, status='failed')
        schedule_retry(failed.pk, 'NSX_UNAVAILABLE')
        _gate(self.a)  # Does not raise while the retry waits.
        with self.assertRaisesMessage(ValidationError, 'An automatic retry is waiting'):
            enqueue(self.a, None, testing=True)

    def test_worker_lane_passes_its_slot_to_reconciliation_and_claims(self):
        from unittest.mock import MagicMock
        from .management.commands.audit_worker import Command
        command = Command()
        command.lane_connections = [MagicMock()]
        with patch.object(command, 'check_lease'), patch('inventory.task_cancellation.reconcile'), \
             patch('inventory.management.commands.audit_worker.expire_jobs'), \
             patch('inventory.management.commands.audit_worker.reconcile_orphaned_collections') as reconcile, \
             patch('inventory.management.commands.audit_worker.claim_job', return_value=None) as claim:
            command.run_worker({'lane': 'collection_2', 'once': True})
        reconcile.assert_called_once_with('collection_2')
        claim.assert_called_once_with('collection_2')

    def collect(self, outcome):
        from .credentials import encrypt_password
        from .services import engine, execute_job
        from .tests import sample_report
        self.a.password_ciphertext = encrypt_password('synthetic-password'); self.a.save()
        job = enqueue(self.a, None)
        self.assertEqual(claim_job().pk, job.pk)
        audit = {'return_value': sample_report()} if outcome is None else {'side_effect': outcome}
        with patch.object(engine(), 'NSXClient') as client, patch.object(engine(), 'audit', **audit):
            client.return_value.base_url = 'https://a.example.test/policy/api/v1'
            execute_job(job.pk)
        job.refresh_from_db()
        return job

    def test_published_snapshot_saves_trend_figures(self):
        job = self.collect(None)
        self.assertEqual(job.status, 'succeeded', job.error)
        figures = job.snapshot.summary['performance']
        for key in ('elapsed_seconds', 'requests', 'throttled', 'concurrency_final', 'concurrency_peak', 'unknown_membership', 'search_incomplete'):
            self.assertIn(key, figures)
        self.assertEqual(job.snapshot.summary['firewall']['version'], 1, 'Firewall Summary figures are saved at publish.')

    def test_temporary_nsx_failure_queues_exactly_one_retry(self):
        job = self.collect(AuditError('Service unavailable', status_code=503))
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.diagnostics['error']['code'], 'NSX_UNAVAILABLE')
        retry = job.retries.get()
        self.assertEqual((retry.status, retry.retry_of_id), ('queued', job.pk))
        denied = AuditJob.objects.create(environment=self.b, status='failed')
        self.assertIsNone(schedule_retry(denied.pk, failure_code(AuditError('denied', status_code=403))))

    def test_retry_happens_once_and_never_for_permanent_or_paused_cases(self):
        failed = AuditJob.objects.create(environment=self.a, status='failed')
        self.assertIsNone(schedule_retry(failed.pk, 'NSX_ACCESS_DENIED'))
        retry = schedule_retry(failed.pk, 'NSX_UNAVAILABLE')
        self.assertIsNotNone(retry)
        retry.status = 'failed'; retry.save()
        self.assertIsNone(schedule_retry(retry.pk, 'NSX_UNAVAILABLE'), 'A retry is never retried.')
        testing = AuditJob.objects.create(environment=self.b, status='failed', testing=True)
        self.assertIsNone(schedule_retry(testing.pk, 'NETWORK_TIMEOUT'))
        Environment.objects.filter(pk=self.c.pk).update(enabled=False)
        paused = AuditJob.objects.create(environment=self.c, status='failed')
        self.assertIsNone(schedule_retry(paused.pk, 'NETWORK_TIMEOUT'))

    def test_requested_collection_starts_a_waiting_retry_now(self):
        failed = AuditJob.objects.create(environment=self.a, status='failed')
        retry = schedule_retry(failed.pk, 'NETWORK_ERROR')
        started = enqueue(self.a, None)
        self.assertEqual(started.pk, retry.pk)
        self.assertIsNone(started.not_before)
        with self.assertRaises(ValidationError):
            enqueue(self.a, None)


class SearchGapTests(SimpleTestCase):
    def test_incomplete_search_publishes_with_failed_coverage(self):
        groups = [{'path': '/infra/domains/default/groups/a'}, {'path': '/infra/domains/default/groups/b'}]
        with patch.object(collector, 'search_configuration', return_value=([groups[0]], {'query': 'q', 'mode': 'all_types'})), \
             patch.object(collector.time, 'sleep'):
            resources, coverage = collector.verified_configuration_search(object(), groups, [])
        self.assertEqual(resources, [groups[0]])
        self.assertTrue(coverage['failed'])
        self.assertEqual(coverage['status'], 'incomplete')
        self.assertIn('missing 1 inventory objects', coverage['errors'][0])
        self.assertTrue(relevant(issues({'search_coverage': coverage}), 'unused', '/infra/domains/default/groups/b'))
        self.assertFalse(relevant(issues({'search_coverage': coverage}), 'empty_group', '/infra/domains/default/groups/b'))


class SearchGapFindingTests(TestCase):
    def test_unknown_usage_pauses_existing_unused_finding(self):
        from .findings import synchronize
        from .test_foundations import report
        environment = Environment.objects.create(name='Search gap', slug='search-gap', manager='https://example.test')
        FindingPolicy.objects.create(empty_group_days=0, unused_service_days=0, unused_days=0)
        data = report()
        service = {'kind': 'custom_service', 'path': '/infra/services/test', 'name': 'Service',
                   'membership': 'not_applicable', 'usage': 'unused_candidate', 'referenced_by': []}
        data['objects'].append(service)
        first = Snapshot.objects.create(environment=environment, generated_at=timezone.now() - timedelta(minutes=1), report=data,
                                        summary={'evidence_coverage': issues(data)})
        synchronize(environment.pk)
        finding = Finding.objects.get(environment=environment, kind='unused_service')
        finding.workflow_state = 'ready'; finding.approvals = {'owner': {'saved': 'decision'}}; finding.save()
        later = report()
        later['objects'].append({**service, 'usage': 'unknown'})
        later['search_coverage'] = {'mode': 'all_types', 'status': 'incomplete', 'failed': True, 'errors': ['Search results are missing 1 inventory objects']}
        self.assertTrue(collector.needs_review(later))
        Snapshot.objects.create(environment=environment, generated_at=timezone.now(), report=later,
                                summary={'evidence_coverage': issues(later)})
        synchronize(environment.pk)
        finding.refresh_from_db()
        self.assertTrue(finding.present, 'A search gap is not evidence that the object became used.')
        self.assertEqual(finding.snapshot_id, first.pk)
        self.assertEqual((finding.workflow_state, finding.approvals), ('ready', {'owner': {'saved': 'decision'}}))


class NetworkOverlapTests(SimpleTestCase):
    def strip(self, value):
        if isinstance(value, dict):
            return {key: self.strip(item) for key, item in value.items()
                    if not key.endswith('_at') and key not in ('performance', 'generated_at', 'seconds')}
        if isinstance(value, list):
            return [self.strip(item) for item in value]
        return value

    def test_overlapped_network_phase_saves_the_same_evidence(self):
        from . import test_network_membership as fixtures
        original = fixtures.synthetic_inventory

        def tagged_inventory(group_count=2):
            # Tagged VMs and one marked for deletion, so a different VM list would show.
            client, groups, vms, *rest = original(group_count)
            vms[0]['tags'] = [{'scope': 'app', 'tag': 'web'}]
            vms.append({'path': '/infra/realized-state/virtual-machines/deleted', 'external_id': 'vm-deleted',
                        'display_name': 'Deleted VM', 'tags': [{'scope': 'app', 'tag': 'web'}], 'marked_for_delete': True})
            return (client, groups, vms, *rest)
        with patch.object(fixtures, 'synthetic_inventory', tagged_inventory):
            overlapped, _ = fixtures.synthetic_report()
            with patch.object(collector, 'OVERLAP_NETWORK_PHASE', False):
                sequential, _ = fixtures.synthetic_report()
        names = [vm['display_name'] for vm in overlapped['tags']['virtual_machines']]
        self.assertNotIn('Deleted VM', names)
        self.assertIn('network_interfaces', overlapped['tags']['virtual_machines'][0])
        for key in ('tags', 'inventory', 'membership_evidence', 'objects'):
            self.assertEqual(self.strip(overlapped[key]), self.strip(sequential[key]), key)
        self.assertTrue(overlapped['performance']['membership'].get('network_overlapped_with_firewall'))
        self.assertNotIn('network_overlapped_with_firewall', sequential['performance']['membership'])
