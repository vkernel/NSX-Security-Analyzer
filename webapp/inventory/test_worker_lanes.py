from unittest.mock import patch, MagicMock
from contextlib import ExitStack
from django.test import TestCase, SimpleTestCase
from django.db import connection
from .models import Environment, AuditJob
from .services import claim_job
from . import finding_recalculation
from .worker_lanes import lease, LANES
from .management.commands.audit_worker import Command


class LaneClaimsTests(TestCase):
    def test_claims_succeed_while_all_worker_leases_are_held(self):
        if connection.vendor != 'postgresql': self.skipTest('PostgreSQL advisory locks')
        env = Environment.objects.create(name='Concurrent', slug='concurrent', manager='https://example.test')
        AuditJob.objects.create(environment=env)
        finding_recalculation.queue([env.pk], ['empty_group'])
        with ExitStack() as stack:
            for lane in LANES:
                self.assertIsNot(stack.enter_context(lease(lane)), False)
            # Fail quickly if a lifetime lease collides with a claim lock.
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '1s'")
            self.assertIsNotNone(claim_job())
            self.assertIsNotNone(finding_recalculation.claim())
            self.assertIsNone(claim_job())
            self.assertIsNone(finding_recalculation.claim())

    def test_one_collection_and_one_recalculation_can_be_claimed(self):
        a=Environment.objects.create(name='A',slug='lane-a',manager='https://a.example')
        b=Environment.objects.create(name='B',slug='lane-b',manager='https://b.example')
        AuditJob.objects.create(environment=a)
        AuditJob.objects.create(environment=b)
        finding_recalculation.queue([a.pk,b.pk], ['empty_group'])
        self.assertIsNotNone(claim_job())
        self.assertIsNotNone(finding_recalculation.claim())
        self.assertIsNone(claim_job())
        self.assertIsNone(finding_recalculation.claim())

    def test_database_lane_leases_are_independent_and_exclusive(self):
        if connection.vendor!='postgresql': self.skipTest('PostgreSQL session locks')
        with lease('collection') as collection:
            self.assertIsNot(collection,False)
            with lease('collection') as duplicate:
                self.assertIs(duplicate,False)
            with lease('recalculation') as recalculation:
                self.assertIsNot(recalculation,False)
                with lease('recalculation') as duplicate:
                    self.assertIs(duplicate,False)
                with lease('comparison') as comparison:
                    self.assertIsNot(comparison,False)
                    with lease('comparison') as duplicate:
                        self.assertIs(duplicate,False)
        with lease('collection') as released:
            self.assertIsNot(released,False)

    def test_legacy_queued_wording_is_normalized(self):
        job=AuditJob(progress_stage='Waiting for an audit worker')
        self.assertEqual(job.progress['stage'],'Waiting for a collection worker')


class SupervisorTests(SimpleTestCase):
    def test_supervisor_starts_and_stops_all_four_lanes(self):
        processes=[MagicMock() for _ in LANES]
        for process in processes: process.poll.return_value=None
        with patch('inventory.management.commands.audit_worker.subprocess.Popen',side_effect=processes) as spawn, patch('inventory.management.commands.audit_worker.time.sleep',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): Command().supervise_lanes()
        self.assertEqual(spawn.call_args_list[0].args[0][-1],'collection')
        self.assertEqual(spawn.call_args_list[1].args[0][-1],'recalculation')
        self.assertEqual(spawn.call_args_list[2].args[0][-1],'comparison')
        self.assertEqual(spawn.call_args_list[3].args[0][-1], 'deletion_preview')
        for process in processes:
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=15)

    def test_each_lane_claims_only_its_own_queue(self):
        for lane in LANES:
            with self.subTest(lane=lane):
                command = Command()
                with patch.object(command, 'run_deletion', return_value=False) as deletion, patch.object(command, 'check_lease'), \
                     patch('inventory.management.commands.audit_worker.expire_jobs'), \
                     patch('inventory.management.commands.audit_worker.claim_job', return_value=None) as collection, \
                     patch('inventory.finding_recalculation.claim', return_value=None) as recalculation, \
                     patch.object(command, 'run_comparison', return_value=False) as comparison:
                    command.run_worker({'lane': lane, 'once': True})
                self.assertEqual(collection.call_count, int(lane == 'collection'))
                self.assertEqual(recalculation.call_count, int(lane == 'recalculation'))
                self.assertEqual(comparison.call_count, int(lane == 'comparison'))
                self.assertEqual(command.heartbeat_lanes, (lane,))
                if lane == 'deletion_preview': deletion.assert_called_once_with(preview=True)
                elif lane == 'collection': deletion.assert_called_once_with()
                else: deletion.assert_not_called()

    def test_comparison_runs_while_other_queues_are_busy(self):
        command = Command()
        with patch.object(command, 'run_deletion', return_value=False), patch.object(command, 'check_lease'), \
             patch('inventory.management.commands.audit_worker.claim_job') as collection, \
             patch('inventory.finding_recalculation.claim') as recalculation, \
             patch.object(command, 'run_comparison', return_value=True) as comparison:
            command.run_worker({'lane': 'comparison', 'once': True})
        comparison.assert_called_once()
        collection.assert_not_called()
        recalculation.assert_not_called()

    def test_default_and_legacy_alias_supervise_all_lanes(self):
        for args in ([], ['--lane', 'both']):
            command = Command()
            options = vars(command.create_parser('manage.py', 'audit_worker').parse_args(args))
            with patch.object(command, 'supervise_lanes') as supervise:
                command.handle(**options)
            supervise.assert_called_once()

class DeletionLaneTests(TestCase):
    def setUp(self):
        from .models import DeletionBatch
        self.env = Environment.objects.create(name='Preview', slug='preview', manager='https://example.test')
        self.preview = DeletionBatch.objects.create(environment=self.env, manager=self.env.manager)
        self.execution = DeletionBatch.objects.create(environment=self.env, manager=self.env.manager, status='queued')

    def test_preview_claim_does_not_claim_execution_or_wait_for_collection(self):
        from .deletion import claim
        AuditJob.objects.create(environment=self.env, status='running')
        self.assertEqual(claim(preview=True).pk, self.preview.pk)
        self.assertIsNone(claim(preview=True))
        self.execution.refresh_from_db()
        self.assertEqual(self.execution.status, 'queued')
        self.assertEqual(claim().pk, self.execution.pk)
        self.assertIsNone(claim())

    def test_reconciliation_never_interrupts_other_lane(self):
        for preview in (True, False):
            self.preview.status = 'checking'; self.preview.save()
            self.execution.status = 'running'; self.execution.save()
            with patch('inventory.deletion.claim', return_value=None):
                self.assertFalse(Command().run_deletion(preview=preview))
            self.preview.refresh_from_db(); self.execution.refresh_from_db()
            self.assertEqual(self.preview.status, 'interrupted' if preview else 'checking')
            self.assertEqual(self.execution.status, 'running' if preview else 'interrupted')

    def test_preview_lease_is_exclusive_and_independent(self):
        if connection.vendor != 'postgresql': self.skipTest('PostgreSQL advisory locks')
        with lease('collection') as collection, lease('deletion_preview') as preview:
            self.assertIsNot(collection, False)
            self.assertIsNot(preview, False)
            with lease('deletion_preview') as duplicate:
                self.assertIs(duplicate, False)

    def test_queue_labels_explain_separate_lanes(self):
        self.assertEqual(self.preview.status_label, 'Waiting for preview worker')
        self.assertEqual(self.execution.status_label, 'Waiting for collection lane to become available')
