import json
import logging
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import OperationalError, transaction
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import AuditEvent, AuditJob, Environment
from .audit_events import record, cleanup
from .observability import ConsoleFormatter, exception_details, CONTEXT, ScopedDebug


class FormattingTests(SimpleTestCase):
    def test_json_redacts_exception_chain_and_keeps_location(self):
        try:
            try:
                raise ValueError('password="private value" Authorization: Bearer abc.def.xyz')
            except ValueError as inner:
                raise RuntimeError('request failed\nforged log') from inner
        except RuntimeError as exc:
            record = logging.LogRecord('inventory', logging.ERROR, __file__, 1, 'failed', (), (type(exc), exc, exc.__traceback__))
            result = ConsoleFormatter().format(record)
        payload = json.loads(result)
        self.assertEqual(len(payload['error']['chain']), 2)
        self.assertNotIn('private value', result)
        self.assertNotIn('abc.def.xyz', result)
        self.assertNotIn('\n', result)
        self.assertTrue(payload['error']['chain'][0]['frames'][0]['file'])

    def test_database_details_exclude_sql_and_identify_lock(self):
        class DatabaseCause(Exception):
            sqlstate = '55P03'
        try:
            try:
                raise DatabaseCause('SQL contains confidential snapshot')
            except DatabaseCause as cause:
                raise OperationalError('INSERT confidential snapshot') from cause
        except OperationalError as exc:
            result = exception_details(exc)
        self.assertEqual(result['code'], 'DATABASE_LOCK_TIMEOUT')
        self.assertNotIn('confidential', json.dumps(result))

    def test_scoped_debug_expires(self):
        row = logging.LogRecord('nsx_inventory', logging.DEBUG, '', 0, 'request', (), None)
        self.assertTrue(ScopedDebug(timezone.now()+timedelta(minutes=1)).filter(row))
        self.assertFalse(ScopedDebug(timezone.now()-timedelta(seconds=1)).filter(row))
        row.levelno = logging.WARNING
        self.assertTrue(ScopedDebug(None).filter(row))


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AuditTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('audit-admin', password='test-pass', is_staff=True, is_superuser=True)
        self.viewer = get_user_model().objects.create_user('audit-viewer', password='test-pass')
        self.env = Environment.objects.create(name='Synthetic', slug='synthetic', manager='https://manager.example.invalid')

    def test_committed_changes_have_safe_diff_and_survive_deletion(self):
        self.env.password_ciphertext = 'never-log-ciphertext'
        self.env.sync_interval_minutes = 120
        self.env.save()
        event = AuditEvent.objects.filter(action='environment.updated').first()
        self.assertTrue(event.details['credentials_or_trust_changed'])
        self.assertNotIn('never-log-ciphertext', json.dumps(event.details))
        target = str(self.env.pk)
        self.env.delete()
        self.admin.delete()
        self.assertTrue(AuditEvent.objects.filter(target_id=target, action='environment.updated').exists())

    def test_rolled_back_change_has_no_success_event(self):
        count = AuditEvent.objects.count()
        try:
            with transaction.atomic():
                self.env.enabled = False
                self.env.save()
                raise ValueError('rollback')
        except ValueError:
            pass
        self.assertEqual(AuditEvent.objects.count(), count)

    def test_audit_store_failure_emits_external_fallback(self):
        with patch('inventory.models.AuditEvent.objects.create', side_effect=OperationalError('password=secret')):
            with self.assertLogs('inventory.audit', level='ERROR') as captured:
                record('auth.login', outcome='failed', best_effort=True)
        self.assertIn('audit persistence failed', captured.output[0])
        self.assertNotIn('password', captured.output[0])

    def test_pages_permissions_export_and_request_context(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('audit-log'))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(AuditEvent.objects.filter(action='access.denied', request_id=response['X-Request-ID']).exists())
        self.assertEqual(CONTEXT.get(), {})
        self.client.force_login(self.admin)
        response = self.client.get(reverse('audit-log'))
        self.assertContains(response, 'Diagnostic collection')
        response = self.client.get(reverse('audit-log'), {'export': 'json', 'q': 'auth.login'})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)
        self.assertTrue(AuditEvent.objects.filter(action='audit.exported').exists())

    def test_diagnostic_collection_is_bounded_and_does_not_enable_global_debug(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('diagnostic-collection'), {'environment': self.env.pk})
        self.assertEqual(response.status_code, 302)
        job = AuditJob.objects.get(environment=self.env)
        self.assertLessEqual(job.debug_until, timezone.now()+timedelta(minutes=15))
        self.assertContains(self.client.get(response.url), str(job.pk))
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(response.url).status_code, 403)

    def test_collection_statistics_are_staff_only_and_do_not_load_full_report(self):
        from .models import Snapshot
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        snapshot = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report={
            'performance': {'http': {'requests': 123, 'retries': 2}},
            'dfw': {'collection_diagnostics': {'bulk_successes': 10}},
            'objects': [{'name': 'unrelated-inventory-data'}]})
        job = AuditJob.objects.create(environment=self.env, status='succeeded')
        Snapshot.objects.filter(pk=snapshot.pk).update(job=job)
        url = reverse('collection-diagnostics', args=[job.pk])
        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertContains(response, 'Request statistics and concurrency')
        self.assertContains(response, '123')
        self.assertNotContains(response, 'unrelated-inventory-data')
        self.assertFalse(any('SELECT "inventory_snapshot"."report" FROM' in q['sql'] for q in queries))
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_failed_auth_has_no_submitted_secret(self):
        self.client.post(reverse('login'), {'username': 'unknown', 'password': 'not-for-logs'})
        event = AuditEvent.objects.filter(action='auth.login', outcome='failed').first()
        self.assertIsNotNone(event)
        self.assertNotIn('not-for-logs', json.dumps(event.details))

    @override_settings(AUDIT_EVENT_RETENTION_DAYS=7)
    def test_retention_is_independent_and_audited(self):
        event = record('synthetic.old')
        AuditEvent.objects.filter(pk=event.pk).update(created_at=timezone.now()-timedelta(days=8))
        cleanup()
        self.assertFalse(AuditEvent.objects.filter(pk=event.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action='audit.retention').exists())

    def test_handled_collection_failure_persists_timeline_and_safe_cause(self):
        from unittest.mock import Mock
        from .credentials import encrypt_password
        from .services import enqueue, claim_job, execute_job
        self.env.username = 'reader'
        self.env.password_ciphertext = encrypt_password('synthetic-private-value')
        self.env.save()
        job = enqueue(self.env, self.admin, testing=True)
        claim_job()
        client = Mock()
        client.base_url = self.env.manager + '/policy/api/v1'
        with patch('inventory.collector.NSXClient', return_value=client), patch(
                'inventory.collector.audit', side_effect=TimeoutError('password=synthetic-private-value')):
            execute_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.diagnostics['error']['code'], 'NETWORK_TIMEOUT')
        self.assertTrue(any(step['outcome'] == 'interrupted' for step in job.diagnostics['timeline']))
        self.assertNotIn('synthetic-private-value', json.dumps(job.diagnostics))
        self.assertNotIn('synthetic-private-value', job.error)
