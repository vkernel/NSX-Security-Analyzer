"""Bound concurrent NSX requests using observed latency and backpressure."""
from collections import Counter, deque
import math
import threading
import time
import urllib3


class AdaptiveRequests:
    def __init__(self, maximum=8, clock=time.monotonic):
        if type(maximum) is not int or not 1 <= maximum <= 32:
            raise ValueError("Concurrency must be between 1 and 32")
        self.maximum = maximum
        self.limit = min(2, maximum)
        self.initial = self.limit
        self.peak = self.limit
        self.active = 0
        self.successes = 0
        self.contended = False
        self.last_decrease = float('-inf')
        self.increases = self.decreases = 0
        self.clock = clock
        self.condition = threading.Condition()
        self.latencies = {}
        self.waiters = deque()
        self.queue_seconds = Counter()

    def acquire(self, deadline=None, endpoint='other'):
        queued = self.clock()
        ticket = object()
        with self.condition:
            self.waiters.append(ticket)
            try:
                while True:
                    if deadline is not None and self.clock() >= deadline:
                        raise TimeoutError('Request phase budget exhausted while waiting for concurrency')
                    if self.waiters[0] is ticket and self.active < self.limit:
                        self.waiters.popleft()
                        self.active += 1
                        self.queue_seconds[endpoint] += max(0, self.clock() - queued)
                        self.condition.notify_all()
                        return self.clock()
                    self.contended = True
                    self.condition.wait(timeout=max(.001, deadline - self.clock()) if deadline is not None else None)
            finally:
                if ticket in self.waiters:
                    self.waiters.remove(ticket)
                    self.condition.notify_all()

    def release(self, started, error=None, endpoint="other"):
        elapsed = self.clock() - started
        with self.condition:
            self.active -= 1
            # An optional bulk endpoint's read timeout is not by itself evidence
            # of manager-wide overload. Explicit backpressure still applies.
            cause = getattr(error, "__cause__", None)
            optional_timeout = (endpoint == "policy_statistics" and
                                isinstance(cause, (TimeoutError, urllib3.exceptions.ReadTimeoutError)))
            overloaded = (error is not None and not optional_timeout and
                          getattr(error, 'status_code', None) in (None, 429, 502, 503, 504))
            # Compare like endpoints. Statistics can be naturally slow without
            # indicating overload; one slow response must not serialize the audit.
            state = self.latencies.setdefault(endpoint, {"samples": 0, "baseline": elapsed,
                                                         "slow_streak": 0, "seconds": 0.0, "failures": Counter(),
                                                         "durations": deque(maxlen=512), "maximum": 0.0})
            state["seconds"] += elapsed
            state["durations"].append(elapsed)
            state["maximum"] = max(state["maximum"], elapsed)
            if error is not None:
                state["failures"][str(getattr(error, "status_code", None) or "transport_or_decode")] += 1
            degraded = False
            if error is None:
                slow = state["samples"] >= 4 and elapsed > max(state["baseline"] * 2, state["baseline"] + 2)
                state["slow_streak"] = state["slow_streak"] + 1 if slow else 0
                if not slow:
                    state["baseline"] = state["baseline"] * .8 + elapsed * .2
                state["samples"] += 1
                degraded = state["slow_streak"] >= 3
            else:
                state["slow_streak"] = 0
            if overloaded or (degraded and self.clock() - self.last_decrease >= 30):
                reduced = max(1, self.limit // 2)
                self.decreases += int(reduced < self.limit)
                self.limit = reduced
                self.successes = 0
                self.contended = False
                self.last_decrease = self.clock()
            elif error is None and not state["slow_streak"]:
                self.successes += 1
                if (self.contended and self.successes >= max(8, self.limit * 4)
                        and self.clock() - self.last_decrease >= 30 and self.limit < self.maximum):
                    self.limit += 1
                    self.peak = max(self.peak, self.limit)
                    self.increases += 1
                    self.successes = 0
                    self.contended = False
            elif not optional_timeout:
                self.successes = 0
            self.condition.notify_all()

    def summary(self):
        with self.condition:
            return {'mode': 'automatic', 'scheduling': 'fifo', 'queue_wait_seconds': dict(self.queue_seconds), 'initial': self.initial, 'peak': self.peak,
                    'final': self.limit, 'maximum': self.maximum,
                    'increases': self.increases, 'decreases': self.decreases,
                    'endpoints': {key: {'successful_requests': value['samples'],
                                        'failed_requests': sum(value['failures'].values()),
                                        'failures_by_status': dict(value['failures']),
                                        'latency_sample_count': len(value['durations']),
                                        'p95_seconds': round(sorted(value['durations'])[math.ceil(len(value['durations']) * .95) - 1], 3),
                                        'max_seconds': round(value['maximum'], 3),
                                        'request_seconds': round(value['seconds'], 3),
                                        'baseline_seconds': round(value['baseline'], 3)}
                                  for key, value in self.latencies.items()}}


def adapt_requests(client, maximum=8):
    controller = AdaptiveRequests(maximum=maximum)
    from .request_pacing import RequestPacer
    pacer = RequestPacer(deadline=getattr(client, "request_deadline", None))
    client.request_pacer = pacer
    client.request_context = threading.local()
    original = client._get

    def request(path, params=None):
        endpoint = ("rule_statistics" if "/rules/" in path else "policy_statistics") if path.endswith("/statistics") else (
            "membership/" + path.rsplit("/", 1)[-1] if "/members/" in path else "search" if path.startswith("/search/") else "inventory")
        queued_at = time.monotonic()
        phase_deadline = getattr(client.request_context, 'phase_deadline', None)
        started = controller.acquire(deadline=phase_deadline, endpoint=endpoint)
        error = None
        try:
            # Queue time is not time spent attempting a request. The whole
            # collection deadline still bounds pacing and server cooldowns.
            pacer.acquire(deadline=phase_deadline)
            waited = time.monotonic() - queued_at
            if hasattr(client.request_context, 'deadline'):
                client.request_context.deadline = min(client.request_context.deadline + waited,
                                                      getattr(client, 'request_deadline', float('inf')),
                                                      phase_deadline if phase_deadline is not None else float('inf'))
            started = controller.clock()
            return original(path, params)
        except BaseException as exc:
            error = exc
            raise
        finally:
            pacer.result(error)
            controller.release(started, error, endpoint)

    # Limit each attempt; NSXClient.get releases the slot before retry backoff.
    client._get = request
    return controller
