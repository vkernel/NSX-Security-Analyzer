import io
import subprocess
import sys
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from inventory.models import Environment, AuditJob, CollectionStopRequest, Snapshot, AuditEvent
from inventory.credentials import encrypt_password
from inventory.services import enqueue, claim_job, request_collection_stop, finish_stopped_job, execute_job, engine
from inventory.tests import sample_report


@override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class CancellationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('stop-operator', is_staff=True)
        self.environment = Environment.objects.create(slug='stop-test', name='Stop test',
            manager='https://east.example', username='reader', password_ciphertext=encrypt_password('test-only'))
        self.job = enqueue(self.environment, self.user)
        self.client.force_login(self.user)

    def test_queued_stop_is_immediate_and_logged(self):
        response = self.client.post(reverse('stop-collection', args=[self.job.pk]))
        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'cancelled')
        self.assertIsNotNone(self.job.finished_at)
        self.assertIsNone(claim_job())
        self.assertTrue(AuditEvent.objects.filter(action='collection.stop_requested', actor_id_text=str(self.user.pk)).exists())
        self.assertTrue(AuditEvent.objects.filter(action='collection.stopped').exists())
        self.assertFalse(request_collection_stop(self.job.pk, self.user))

    def test_stop_requires_staff_post_and_csrf(self):
        url = reverse('stop-collection', args=[self.job.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(url).status_code, 403)
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertFalse(CollectionStopRequest.objects.exists())

    def test_running_request_is_idempotent_and_keeps_environment_busy(self):
        claim_job()
        request_collection_stop(self.job.pk, self.user)
        request_collection_stop(self.job.pk, self.user)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'running')
        self.assertEqual(CollectionStopRequest.objects.count(), 1)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            enqueue(self.environment, self.user)
        response = self.client.get(reverse('collection-history', args=[self.environment.pk]))
        self.assertContains(response, 'Stop requested')

    def test_supervisor_kills_real_collector_process(self):
        from inventory.management.commands.audit_worker import Command
        claim_job()
        request_collection_stop(self.job.pk, self.user)
        with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']) as child:
            Command().wait_for_collection(child, self.job.pk)
            self.assertIsNotNone(child.poll())
            self.assertNotEqual(child.returncode, 0)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'cancelled')
        self.assertFalse(Snapshot.objects.exists())
        enqueue(self.environment, self.user)  # Worker acknowledgement releases environment.

    def test_polling_timeout_does_not_end_collection_early(self):
        from inventory.management.commands.audit_worker import Command
        from unittest.mock import Mock
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('run_audit', 2), 0]
        with patch('inventory.management.commands.audit_worker.time.monotonic', side_effect=[0, 1, 3]):
            Command().wait_for_collection(child, self.job.pk)
        self.assertEqual(child.wait.call_count, 2)
        child.kill.assert_not_called()

    def test_completion_wins_race_without_relabelling_saved_snapshot(self):
        AuditJob.objects.filter(pk=self.job.pk).update(status='succeeded')
        self.assertFalse(finish_stopped_job(self.job.pk))
        self.assertFalse(request_collection_stop(self.job.pk, self.user))
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'succeeded')

    def test_request_during_indexing_rolls_back_snapshot(self):
        claim_job()
        audit = engine()
        with patch.object(audit, 'NSXClient') as client, patch.object(audit, 'audit', return_value=sample_report()), \
                patch('inventory.services.stop_requested', side_effect=[False, True]):
            client.return_value.base_url = 'https://east.example/policy/api/v1'
            execute_job(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'cancelled')
        self.assertFalse(Snapshot.objects.exists())


from django.test import TransactionTestCase


class CancellationLockTests(TransactionTestCase):
    def test_stop_request_does_not_wait_for_snapshot_row_lock(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import connection, connections, transaction
        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row locks')
        user = get_user_model().objects.create_user('lock-operator', is_staff=True)
        environment = Environment.objects.create(slug='lock-test', name='Lock test',
            manager='https://east.example', username='reader')
        job = AuditJob.objects.create(environment=environment, status='running')
        locked, release = Event(), Event()

        def saving_snapshot():
            try:
                with transaction.atomic():
                    AuditJob.objects.select_for_update().get(pk=job.pk)
                    locked.set()
                    release.wait(10)
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(saving_snapshot)
            try:
                self.assertTrue(locked.wait(5))
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '1500ms'")
                self.assertTrue(request_collection_stop(job.pk, user))
                self.assertTrue(CollectionStopRequest.objects.filter(job_id=job.pk).exists())
            finally:
                release.set()
                future.result(timeout=5)
                with connection.cursor() as cursor:
                    cursor.execute('RESET lock_timeout')


class CancellationDatabaseFailureTests(TestCase):
    def test_timeout_settings_are_restored(self):
        from django.db import connection
        from inventory.services import cancellation_database_timeout
        with connection.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            original = cursor.fetchone()[0]
        with cancellation_database_timeout():
            with connection.cursor() as cursor:
                cursor.execute("SHOW statement_timeout")
                self.assertEqual(cursor.fetchone()[0], '5s')
        with connection.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            self.assertEqual(cursor.fetchone()[0], original)

    def test_exclusions_alone_do_not_make_collection_incomplete(self):
        from inventory.coverage_index import report_issues
        report = sample_report()
        report['objects'] = []
        report['tags'] = {}
        report['dfw'] = {'rules': [], 'policies': [], 'errors': []}
        report['search_coverage'] = {'mode': 'all_types'}
        report['inventory'] = {'groups': [{'membership': 'not_assessed',
            'audit_exclusions': ['System-owned object']}]}
        self.assertFalse(engine().needs_review(report))
        self.assertEqual(list(report_issues(report)), [])

    def test_real_failure_still_requires_review(self):
        report = sample_report()
        report['objects'] = [{'membership': 'unknown', 'notes': ['Request failed']}]
        self.assertTrue(engine().needs_review(report))


class ExtendedMembershipCoverageTests(TestCase):
    def test_successful_empty_probes_with_extended_expression_are_scope_limitation(self):
        from unittest.mock import Mock
        client = Mock()
        client.get.return_value = {'results': [], 'result_count': 0}
        group = {'path': '/infra/domains/default/groups/identity-example',
                 'extended_expression': [{'resource_type': 'IdentityGroupExpression'}]}
        status, notes = engine().membership(client, group)
        self.assertEqual(status, 'not_supported')
        self.assertIn('not assessed as empty', notes[0])
        report = sample_report()
        report.update(objects=[{'membership': status}], dfw={}, tags={})
        self.assertFalse(engine().needs_review(report))

    def test_extended_expression_does_not_hide_failed_probe(self):
        from unittest.mock import Mock
        client = Mock()
        client.get.side_effect = engine().AuditError('request failed')
        status, notes = engine().membership(client, {
            'path': '/infra/domains/default/groups/identity-example',
            'extended_expression': [{'resource_type': 'IdentityGroupExpression'}]})
        self.assertEqual(status, 'unknown')
        self.assertIn('request failed', notes)

    def test_positive_membership_evidence_is_preserved(self):
        from unittest.mock import Mock
        client = Mock()
        client.get.return_value = {'results': [{'id': 'example'}]}
        status, _ = engine().membership(client, {
            'path': '/infra/domains/default/groups/identity-example',
            'extended_expression': [{'resource_type': 'IdentityGroupExpression'}]})
        self.assertEqual(status, 'nonempty')
