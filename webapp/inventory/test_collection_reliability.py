"""Collection keeps going through throttling and transient transport errors, and
retrieval leaves time to publish the snapshot."""
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from . import collector
from .concurrency import AdaptiveRequests
from .services import post_processing_reserve


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
