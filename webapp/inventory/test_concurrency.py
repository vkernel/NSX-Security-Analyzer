import threading
from types import SimpleNamespace
from unittest.mock import Mock
from django.test import SimpleTestCase
from .concurrency import AdaptiveRequests, adapt_requests


class AdaptiveRequestTests(SimpleTestCase):
    def setUp(self):
        self.now = 100
        self.control = AdaptiveRequests(clock=lambda: self.now)

    def success(self, duration=0.1):
        start = self.control.acquire()
        self.now += duration
        self.control.release(start)

    def test_fast_requests_increase_only_when_work_is_waiting_and_stay_bounded(self):
        for _ in range(40):
            self.success()
        self.assertEqual(self.control.limit, 2)
        for _ in range(400):
            self.control.contended = True
            self.success()
        self.assertEqual(self.control.limit, 8)
        self.assertEqual(self.control.summary()['peak'], 8)

    def test_throttle_reduces_limit_and_recovery_waits_for_cooldown(self):
        error = RuntimeError('Busy')
        error.status_code = 429
        start = self.control.acquire()
        self.control.release(start, error)
        self.assertEqual(self.control.limit, 1)
        for _ in range(20):
            self.control.contended = True
            self.success()
        self.assertEqual(self.control.limit, 1)
        self.now += 31
        self.control.contended = True
        self.success()
        self.assertEqual(self.control.limit, 2)

    def test_slow_successes_and_unsupported_endpoints_do_not_reduce(self):
        for _ in range(5):
            self.success(duration=6)
        self.assertEqual(self.control.limit, 2)
        self.control.limit = 4
        error = RuntimeError('Unsupported endpoint')
        error.status_code = 404
        start = self.control.acquire()
        self.control.release(start, error)
        self.assertEqual(self.control.limit, 4)

    def test_waiting_request_is_released_without_exceeding_limit(self):
        control = AdaptiveRequests(maximum=1)
        first = control.acquire()
        attempted, acquired = threading.Event(), threading.Event()
        def work():
            attempted.set()
            start = control.acquire()
            acquired.set()
            control.release(start)
        thread = threading.Thread(target=work)
        thread.start()
        self.assertTrue(attempted.wait(1))
        self.assertFalse(acquired.wait(0.05))
        control.release(first)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(acquired.is_set())
        self.assertEqual(control.active, 0)

    def test_exception_releases_slot_and_preserves_error(self):
        failure = RuntimeError('network failure')
        client = SimpleNamespace(_get=Mock(side_effect=failure))
        control = adapt_requests(client)
        with self.assertRaises(RuntimeError) as raised:
            client._get('/test')
        self.assertIs(raised.exception, failure)
        self.assertEqual(control.active, 0)
        self.assertEqual(control.limit, 1)

    def test_sustained_relative_slowdown_reduces_concurrency(self):
        for _ in range(4):
            self.success(duration=1)
        for _ in range(3):
            self.success(duration=6)
        self.assertEqual(self.control.limit, 1)

    def test_latency_baselines_are_separate_and_slow_stable_work_can_grow(self):
        for _ in range(8):
            self.control.contended = True
            start = self.control.acquire()
            self.now += 12
            self.control.release(start, endpoint='rule_statistics')
        self.assertEqual(self.control.limit, 3)
        for _ in range(8):
            self.success(duration=.1)
        self.assertEqual(self.control.limit, 3)
        self.assertAlmostEqual(self.control.latencies['rule_statistics']['baseline'], 12)
