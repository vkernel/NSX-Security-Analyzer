"""Shared, burst-free pacing for all request attempts in one collection."""
import logging
import random
import threading
import time

LOG = logging.getLogger('inventory.collector')


class CollectionDeadlineExceeded(TimeoutError):
    """Fatal collection deadline, not an individual unsupported/failed check."""


class RequestPacer:
    def __init__(self, clock=time.monotonic, deadline=None):
        self.clock = clock
        self.deadline = deadline
        self.condition = threading.Condition()
        self.rate = 10.0
        self.maximum_rate = 40.0
        self.successes = self.completed = self.failed = 0
        self.started = self.last_report = clock()
        self.last_report_completed = 0
        self.recover_after = 0.0
        self.next_request = self.cooldown = 0.0
        self.last_change = clock()
        self.throttles = 0
        self.wait_seconds = 0.0

    def acquire(self, deadline=None):
        deadline = min(value for value in (deadline, self.deadline, float("inf")) if value is not None)
        with self.condition:
            while True:
                now = self.clock()
                ready = max(self.next_request, self.cooldown)
                if max(now, ready) >= deadline:
                    raise CollectionDeadlineExceeded('Collection time limit reached while waiting for NSX request pacing; earlier saved snapshots remain available')
                if now >= ready:
                    self.next_request = now + 1 / self.rate
                    return
                delay = min(ready-now, 1.0)
                started = self.clock()
                self.condition.wait(delay)
                self.wait_seconds += max(0, self.clock()-started)

    def result(self, error=None):
        with self.condition:
            now = self.clock()
            self.completed += 1
            self.failed += int(error is not None)
            if error is None:
                self.successes += 1
            else:
                self.successes = 0
            if getattr(error, 'status_code', None) == 429:
                self.throttles += 1
                # A simultaneous burst of rejected requests is one rate reduction.
                changed = now >= self.cooldown
                if changed:
                    self.rate = max(.5, self.rate / 2)
                delay = max(getattr(error, 'retry_after', None) or 0, min(30, 10/self.rate) + random.uniform(0, 1))
                self.cooldown = max(self.cooldown, now+delay)
                self.recover_after = self.cooldown + 30
                self.last_change = now
                if changed:
                    LOG.warning('NSX throttling: rate=%.2f requests/s shared_cooldown=%.2fs throttled_attempts=%d', self.rate, self.cooldown-now, self.throttles)
            elif (error is None and now >= self.recover_after and now - self.last_change >= 10
                  and self.successes >= 20 and self.rate < self.maximum_rate):
                self.rate = min(self.maximum_rate, max(self.rate + 1, self.rate * 1.25))
                self.last_change = now
                self.successes = 0
                LOG.info('NSX request rate increased: %.2f requests/s', self.rate)
            if now-self.last_report >= 30:
                LOG.info('NSX request progress: completed=%d failed=%d measured_requests_per_second=%.2f configured_rate=%.2f throttled_attempts=%d aggregate_pacing_wait_seconds=%.2f',
                         self.completed, self.failed, (self.completed-self.last_report_completed)/(now-self.last_report),
                         self.rate, self.throttles, self.wait_seconds)
                self.last_report = now
                self.last_report_completed = self.completed
            self.condition.notify_all()

    def summary(self):
        with self.condition:
            return {'completed_attempts': self.completed, 'failed_attempts': self.failed,
                    'average_requests_per_second': round(self.completed/max(.001, self.clock()-self.started), 2),
                    'maximum_requests_per_second': self.maximum_rate, 'requests_per_second': self.rate, 'throttled_attempts': self.throttles,
                    'aggregate_wait_seconds': round(self.wait_seconds, 3)}
