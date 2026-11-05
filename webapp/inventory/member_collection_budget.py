"""Bound optional browsing reads independently of mandatory membership checks."""
from collections import Counter
from contextlib import contextmanager
import logging
import threading
import time

LOG = logging.getLogger('nsx_inventory')


class DetailBudgetExceeded(TimeoutError):
    pass


class DetailBudget:
    def __init__(self, seconds=300, requests=2000, group_seconds=5, group_requests=8, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + max(0, seconds)
        self.request_limit = max(0, requests)
        self.group_seconds = max(0, group_seconds)
        self.group_requests = max(0, group_requests)
        self.lock = threading.Lock()
        self.last_log = self.started
        self.claimed = 0
        self.stats = {}
        self.stops = Counter()

    def group(self, path):
        return GroupBudget(self, path)

    def record(self, endpoint, **values):
        with self.lock:
            state = self.stats.setdefault(endpoint, Counter())
            state.update(values)
            now = self.clock()
            if now - self.last_log >= 30:
                self.last_log = now
                LOG.info('Member detail progress: requests=%d limit=%d remaining_seconds=%.1f endpoints=%s stops=%s',
                         self.claimed, self.request_limit, max(0, self.deadline-now), dict(self.stats), dict(self.stops))

    def summary(self):
        with self.lock:
            return {'seconds': self.deadline-self.started, 'request_limit': self.request_limit,
                    'requests': self.claimed, 'group_seconds': self.group_seconds,
                    'group_requests': self.group_requests, 'budget_stops': dict(self.stops),
                    'endpoints': {key: dict(value) for key, value in self.stats.items()}}


class GroupBudget:
    def __init__(self, owner, path):
        self.owner, self.path = owner, path
        self.deadline = min(owner.deadline, owner.clock()+owner.group_seconds)
        self.requests = 0

    def fetch(self, client, path, params, *, shared=False):
        from .collector import AuditError
        from .request_pacing import CollectionDeadlineExceeded
        owner = self.owner
        with owner.lock:
            reason = ('time' if owner.clock() >= self.deadline else
                      'collection_requests' if owner.claimed >= owner.request_limit else
                      'group_requests' if self.requests >= owner.group_requests else None)
            if reason:
                owner.stops[reason] += 1
                raise DetailBudgetExceeded('Detailed member collection budget reached: '+reason)
            owner.claimed += 1
            self.requests += 1
        endpoint = path.rsplit('/', 1)[-1]
        started = owner.clock()
        owner.record(endpoint, requests=1, shared_initial_requests=int(shared), additional_requests=int(not shared))
        try:
            with request_deadline(client, self.deadline):
                result = client.get(path, params)
        except CollectionDeadlineExceeded:
            raise
        except (AuditError, TimeoutError) as exc:
            owner.record(endpoint, failures=1, seconds=owner.clock()-started)
            if owner.clock() >= self.deadline or isinstance(exc, TimeoutError):
                raise DetailBudgetExceeded('Detailed member time budget reached') from exc
            raise
        elapsed = owner.clock()-started
        owner.record(endpoint, pages=1, returned_members=len(result.get('results', [])) if isinstance(result, dict) and isinstance(result.get('results'), list) else 0,
                     seconds=elapsed)
        if elapsed >= 2:
            LOG.info('Slow member detail read: group=%s category=%s continuation=%s seconds=%.2f',
                     self.path, endpoint, bool(params.get('cursor')), elapsed)
        return result


@contextmanager
def request_deadline(client, deadline):
    context = getattr(client, 'request_context', None)
    if not isinstance(context, threading.local):
        context = client.request_context = threading.local()
    previous = getattr(context, 'phase_deadline', None)
    previous_member_detail = getattr(context, 'member_detail', None)
    context.phase_deadline = min(previous, deadline) if previous is not None else deadline
    context.member_detail = True
    try:
        yield
    finally:
        if previous is None:
            del context.phase_deadline
        else:
            context.phase_deadline = previous
        if previous_member_detail is None:
            del context.member_detail
        else:
            context.member_detail = previous_member_detail
