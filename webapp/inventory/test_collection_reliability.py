"""Collection keeps going through throttling and transient transport errors, and
retrieval leaves time to publish the snapshot."""
from unittest.mock import Mock, patch
from django.test import SimpleTestCase, TestCase, override_settings
from . import collector
from .concurrency import AdaptiveRequests
from .services import post_processing_reserve, warm_start


def failure(status=None, cause=None):
    error = collector.AuditError('synthetic failure', status_code=status)
    error.__cause__ = cause
    return error


@patch.object(collector.time, 'sleep', new=lambda seconds: None)
class RetryTests(SimpleTestCase):
    def nsx(self, responses, retries=1, paced=True):
        client = collector.NSXClient('https://nsx.invalid', 'reader', 'test-only', retries=retries)
        client._get = Mock(side_effect=responses)
        if paced:
            client.request_pacer = Mock()
        return client

    def test_throttling_waits_on_the_shared_pacer_without_using_retry_attempts(self):
        client = self.nsx([failure(429), failure(429), failure(429), {'results': []}])
        self.assertEqual(client.get('/infra/domains'), {'results': []})
        self.assertEqual(client._get.call_count, 4)

    def test_throttling_without_a_pacer_still_uses_attempts(self):
        client = self.nsx([failure(429), failure(429), {'results': []}], paced=False)
        with self.assertRaises(collector.AuditError):
            client.get('/infra/domains')
        self.assertEqual(client._get.call_count, 2)

    def test_a_transport_error_is_retried_once(self):
        client = self.nsx([failure(cause=OSError('reset')), {'results': []}], retries=2)
        self.assertEqual(client.get('/infra/domains'), {'results': []})
        client = self.nsx([failure(cause=OSError('reset')), failure(cause=OSError('reset')), {'results': []}], retries=2)
        with self.assertRaises(collector.AuditError):
            client.get('/infra/domains')
        self.assertEqual(client._get.call_count, 2)

    def test_local_budget_errors_and_unretryable_statuses_are_not_retried(self):
        local = failure(cause=OSError('never sent'))
        local.local = True
        for error in (local, failure(404)):
            client = self.nsx([error, {'results': []}])
            with self.assertRaises(collector.AuditError):
                client.get('/infra/domains')
            self.assertEqual(client._get.call_count, 1)


class ConcurrencyBackoffTests(SimpleTestCase):
    def controller(self):
        now = [100.0]
        controller = AdaptiveRequests(maximum=8, clock=lambda: now[0])
        controller.limit = 8
        return controller, now

    def release(self, controller, error):
        controller.active += 1
        controller.release(controller.clock(), error, 'inventory')

    def test_a_burst_of_overload_errors_reduces_the_limit_once(self):
        controller, now = self.controller()
        for _ in range(4):
            self.release(controller, failure(503))
        self.assertEqual(controller.limit, 4)
        now[0] += 6
        self.release(controller, failure(503))
        self.assertEqual(controller.limit, 2)

    def test_local_timeouts_are_not_backpressure(self):
        controller, _ = self.controller()
        controller.active += 1
        controller.release(controller.clock(), TimeoutError('phase budget'), 'inventory', local=True)
        self.assertEqual(controller.limit, 8)


class PublicationReserveTests(SimpleTestCase):
    def test_retrieval_leaves_time_to_save_and_index_the_snapshot(self):
        self.assertEqual(post_processing_reserve(3600), 540)
        self.assertEqual(post_processing_reserve(600), 240)
        self.assertEqual(post_processing_reserve(7200), 1080)


class WarmStartTests(TestCase):
    def setUp(self):
        from django.utils import timezone
        from .models import Environment, Snapshot
        self.env = Environment.objects.create(name='Warm start', slug='warm-start', manager='https://nsx.invalid')
        self.snapshot = lambda report: Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report=report)

    def controllers(self):
        from .request_pacing import RequestPacer
        return AdaptiveRequests(maximum=8), RequestPacer()

    def report(self, throttled=0, decreases=0, manager='nsx.invalid'):
        return {'manager': manager, 'performance': {
            'request_pacing': {'requests_per_second': 32.0, 'throttled_attempts': throttled},
            'concurrency': {'final': 8, 'decreases': decreases}}}

    def test_starts_from_three_quarters_of_a_clean_previous_run(self):
        self.snapshot(self.report())
        concurrency, pacer = self.controllers()
        self.assertEqual(warm_start(self.env, 'nsx.invalid', concurrency, pacer), {'requests_per_second': 24.0, 'concurrency': 6})
        self.assertEqual((concurrency.limit, concurrency.initial), (6, 6))

    def test_cold_start_after_throttling_reductions_or_another_manager(self):
        for report in (self.report(throttled=3), self.report(decreases=1), self.report(manager='other.invalid')):
            self.snapshot(report)
            concurrency, pacer = self.controllers()
            self.assertIsNone(warm_start(self.env, 'nsx.invalid', concurrency, pacer))
            self.assertEqual((concurrency.limit, pacer.rate), (2, 10.0))


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PublishedSnapshotIndexingTests(TestCase):
    def test_indexing_failure_keeps_the_snapshot_and_queues_a_rebuild(self):
        from django.utils import timezone
        from .models import Environment, Snapshot
        from .services import index_published_snapshot
        env = Environment.objects.create(name='Index', slug='index-test', manager='https://nsx.invalid')
        snapshot = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={}, browsing_status='indexing')
        with patch('inventory.snapshot_index.build', side_effect=RuntimeError('synthetic indexing failure')):
            index_published_snapshot(None, snapshot)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.browsing_status, 'queued')  # Picked up by the background rebuild lane.

    def test_indexing_page_explains_preparation(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from django.utils import timezone
        from .models import Environment, Snapshot
        env = Environment.objects.create(name='Index page', slug='index-page', manager='https://nsx.invalid')
        snapshot = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={}, browsing_status='indexing')
        self.client.force_login(get_user_model().objects.create_user('index-reader'))
        response = self.client.get(reverse('snapshot', args=[snapshot.pk]))
        self.assertContains(response, 'Preparing browsing data')
        self.assertContains(response, 'findings and reviews are already available')
