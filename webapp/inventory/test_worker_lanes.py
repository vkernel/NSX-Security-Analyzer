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
        with lease('collection') as released:
            self.assertIsNot(released,False)

    def test_legacy_queued_wording_is_normalized(self):
        job=AuditJob(progress_stage='Waiting for an audit worker')
        self.assertEqual(job.progress['stage'],'Waiting for a collection worker')


class SupervisorTests(SimpleTestCase):
    def test_supervisor_stops_both_lanes_on_shutdown(self):
        processes=[MagicMock(),MagicMock()]
        for process in processes: process.poll.return_value=None
        with patch('inventory.management.commands.audit_worker.subprocess.Popen',side_effect=processes) as spawn, patch('inventory.management.commands.audit_worker.time.sleep',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): Command().supervise_lanes()
        self.assertEqual(spawn.call_args_list[0].args[0][-1],'collection')
        self.assertEqual(spawn.call_args_list[1].args[0][-1],'recalculation')
        for process in processes:
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=15)
