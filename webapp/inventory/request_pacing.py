"""Shared, burst-free pacing for all request attempts in one collection."""
import logging
import random
import threading
import time

LOG = logging.getLogger('inventory.collector')


class RequestPacer:
    def __init__(self, clock=time.monotonic, deadline=None):
        self.clock = clock
        self.deadline = deadline
        self.condition = threading.Condition()
        self.rate = 10.0
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
                    raise TimeoutError('Collection request deadline reached during pacing')
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
            if getattr(error, 'status_code', None) == 429:
                self.throttles += 1
                # A simultaneous burst of rejected requests is one rate reduction.
                changed = now >= self.cooldown
                if changed:
                    self.rate = max(.5, self.rate / 2)
                delay = max(getattr(error, 'retry_after', None) or 0, min(30, 10/self.rate) + random.uniform(0, 1))
                self.cooldown = max(self.cooldown, now+delay)
                self.last_change = now
                if changed:
                    LOG.warning('NSX throttling: rate=%.2f requests/s shared_cooldown=%.2fs throttled_attempts=%d', self.rate, self.cooldown-now, self.throttles)
            elif error is None and now - self.last_change >= 30:
                self.rate = min(10, self.rate + .5)
                self.last_change = now
                LOG.info('NSX request rate recovery: %.2f requests/s', self.rate)
            self.condition.notify_all()

    def summary(self):
        with self.condition:
            return {'requests_per_second': self.rate, 'throttled_attempts': self.throttles,
                    'aggregate_wait_seconds': round(self.wait_seconds, 3)}
