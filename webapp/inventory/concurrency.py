"""Bound concurrent NSX requests using observed latency and backpressure."""
import threading
import time


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
            overloaded = error is not None and getattr(error, 'status_code', None) in (None, 429, 502, 503, 504)
            # Compare like endpoints. Statistics can be naturally slow without
            # indicating overload; one slow response must not serialize the audit.
            state = self.latencies.setdefault(endpoint, {"samples": 0, "baseline": elapsed,
                                                         "slow_streak": 0, "seconds": 0.0})
            state["seconds"] += elapsed
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
            else:
                self.successes = 0
            self.condition.notify_all()

    def summary(self):
        with self.condition:
            return {'mode': 'automatic', 'initial': self.initial, 'peak': self.peak,
                    'final': self.limit, 'maximum': self.maximum,
                    'increases': self.increases, 'decreases': self.decreases,
                    'endpoints': {key: {'successful_requests': value['samples'],
                                        'request_seconds': round(value['seconds'], 3),
                                        'baseline_seconds': round(value['baseline'], 3)}
                                  for key, value in self.latencies.items()}}


def adapt_requests(client):
    controller = AdaptiveRequests()
    original = client._get

    def request(path, params=None):
        started = controller.acquire()
        error = None
        try:
            return original(path, params)
        except BaseException as exc:
            error = exc
            raise
        finally:
            endpoint = ("rule_statistics" if "/rules/" in path else "policy_statistics") if path.endswith("/statistics") else (
                "membership" if "/members/" in path else "search" if path.startswith("/search/") else "inventory")
            controller.release(started, error, endpoint)

    # Limit each attempt; NSXClient.get releases the slot before retry backoff.
    client._get = request
    return controller
