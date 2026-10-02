"""Bound concurrent NSX requests using observed latency and backpressure."""
from collections import Counter, deque
import math
import threading
import time
import urllib3


class AdaptiveRequests:
    def __init__(self, maximum=8, clock=time.monotonic):
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

    def acquire(self):
        with self.condition:
            while self.active >= self.limit:
                self.contended = True
                self.condition.wait()
            self.active += 1
        return self.clock()

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
            return {'mode': 'automatic', 'initial': self.initial, 'peak': self.peak,
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


def adapt_requests(client):
    controller = AdaptiveRequests()
    from .request_pacing import RequestPacer
    pacer = RequestPacer(deadline=getattr(client, "request_deadline", None))
    client.request_pacer = pacer
    client.request_context = threading.local()
    original = client._get

    def request(path, params=None):
        started = controller.acquire()
        error = None
        try:
            try:
                pacer.acquire(getattr(client.request_context, "deadline", None))
            except TimeoutError as exc:
                from .collector import AuditError
                raise AuditError(str(exc)) from exc
            started = controller.clock()
            return original(path, params)
        except BaseException as exc:
            error = exc
            raise
        finally:
            endpoint = ("rule_statistics" if "/rules/" in path else "policy_statistics") if path.endswith("/statistics") else (
                "membership" if "/members/" in path else "search" if path.startswith("/search/") else "inventory")
            pacer.result(error)
            controller.release(started, error, endpoint)

    # Limit each attempt; NSXClient.get releases the slot before retry backoff.
    client._get = request
    return controller
