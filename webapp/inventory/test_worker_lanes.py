from unittest.mock import patch, MagicMock
from django.test import TestCase, SimpleTestCase
from django.db import connection
from .models import Environment, AuditJob
from .services import claim_job
from . import finding_recalculation
from .worker_lanes import lease
from .management.commands.audit_worker import Command


class LaneClaimsTests(TestCase):
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
    def test_supervisor_starts_and_stops_all_three_lanes(self):
        processes=[MagicMock(),MagicMock(),MagicMock()]
        for process in processes: process.poll.return_value=None
        with patch('inventory.management.commands.audit_worker.subprocess.Popen',side_effect=processes) as spawn, patch('inventory.management.commands.audit_worker.time.sleep',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): Command().supervise_lanes()
        self.assertEqual(spawn.call_args_list[0].args[0][-1],'collection')
        self.assertEqual(spawn.call_args_list[1].args[0][-1],'recalculation')
        self.assertEqual(spawn.call_args_list[2].args[0][-1],'comparison')
        for process in processes:
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=15)

    def test_each_lane_claims_only_its_own_queue(self):
        for lane in ('collection', 'recalculation', 'comparison'):
            with self.subTest(lane=lane):
                command = Command()
                with patch.object(command, 'check_lease'), \
                     patch('inventory.management.commands.audit_worker.expire_jobs'), \
                     patch('inventory.management.commands.audit_worker.claim_job', return_value=None) as collection, \
                     patch('inventory.finding_recalculation.claim', return_value=None) as recalculation, \
                     patch.object(command, 'run_comparison', return_value=False) as comparison:
                    command.run_worker({'lane': lane, 'once': True})
                self.assertEqual(collection.call_count, int(lane == 'collection'))
                self.assertEqual(recalculation.call_count, int(lane == 'recalculation'))
                self.assertEqual(comparison.call_count, int(lane == 'comparison'))
                self.assertEqual(command.heartbeat_lanes, (lane,))

    def test_comparison_runs_while_other_queues_are_busy(self):
        command = Command()
        with patch.object(command, 'check_lease'), \
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
