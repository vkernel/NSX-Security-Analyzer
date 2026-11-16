"""Age-based cleanup, serialized with collectors and persisted in PostgreSQL."""
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .audit_events import record
from .diagnostics import phase, LOG
from .models import Environment, RetentionPolicy, RetentionState

BATCH_SIZE = 2


def eligible(environment, policy, now):
    snapshots = environment.snapshots.all()
    # Always preserve the latest full snapshot and the latest testing snapshot.
    protected = [pk for testing in (False, True) for pk in
                 snapshots.filter(testing=testing).values_list('pk', flat=True)[:1]]
    # Keep the last verified baseline when newer collections have count warnings.
    protected += list(snapshots.filter(testing=False, imported=False).filter(
        Q(report__inventory_warnings__isnull=True) | Q(report__inventory_warnings=[])
    ).values_list("pk", flat=True)[:1])
    condition = Q(pk__in=[])
    for testing, days in ((False, policy.snapshot_days), (True, policy.testing_days)):
        if days:
            cutoff = now - timedelta(days=days)
            condition |= Q(testing=testing, generated_at__lt=cutoff, created_at__lt=cutoff)
    expired = snapshots.filter(condition).exclude(pk__in=protected)
    jobs = environment.jobs.none()
    if policy.collection_days:
        cutoff = now - timedelta(days=policy.collection_days)
        jobs = environment.jobs.filter(status__in=['succeeded', 'failed', 'cancelled'],
            created_at__lt=cutoff, finished_at__lt=cutoff).filter(
                Q(snapshot__isnull=True) | Q(snapshot__pk__in=expired.values('pk')))
    return expired, jobs


def preview(policy, now=None):
    now = now or timezone.now()
    rows = []
    for environment in Environment.objects.all():
        busy = environment.jobs.filter(status__in=['queued', 'running']).exists()
        snapshots, jobs = eligible(environment, policy, now)
        from .storage_maintenance import browsing_candidates
        rows.append({'browsing': browsing_candidates(environment, policy, now).count(), 'environment': environment, 'busy': busy,
                     'snapshots': snapshots.count(), 'collections': jobs.count(),
                     'state': RetentionState.objects.filter(environment=environment).first()})
    return rows


def cleanup_retention(now=None):
    now = now or timezone.now()
    policy, _ = RetentionPolicy.objects.get_or_create(pk=1)
    if not policy.enabled or any(days and days < 91 for days in (policy.snapshot_days, policy.collection_days)) or (policy.testing_days and policy.testing_days < 1):
        return (0, 0)
    removed_snapshots = removed_jobs = 0
    attempted = False
    # Commit each environment independently; a large backlog cannot hold every
    # environment lock for the duration of the entire workspace cleanup.
    for pk in Environment.objects.values_list('pk', flat=True):
        with transaction.atomic():
            environment = Environment.objects.select_for_update().get(pk=pk)
            state, _ = RetentionState.objects.get_or_create(environment=environment)
            if state.retry_at and state.retry_at > now:
                continue
            attempted = True
            state.checked_at = now
            if environment.jobs.filter(status__in=['queued', 'running']).exists():
                state.status = 'waiting_for_collection'
                state.retry_at = now + timedelta(minutes=1)
            else:
                expired, _ = eligible(environment, policy, now)
                ids = list(expired.order_by('generated_at').values_list('pk', flat=True)[:BATCH_SIZE])
                if ids:
                    environment.snapshots.filter(pk__in=ids).delete()
                    removed_snapshots += len(ids)
                _, jobs = eligible(environment, policy, now)
                ids = list(jobs.filter(snapshot__isnull=True).values_list('pk', flat=True)[:100])
                if ids:
                    environment.jobs.filter(pk__in=ids).delete()
                    removed_jobs += len(ids)
                expired, jobs = eligible(environment, policy, now)
                backlog = expired.exists() or jobs.filter(snapshot__isnull=True).exists()
                state.status = 'backlog' if backlog else 'completed'
                state.retry_at = now + timedelta(minutes=1 if backlog else 60)
                state.completed_at = now
            state.save()
    if attempted:
        RetentionPolicy.objects.filter(pk=1).update(last_run=now, deleted_snapshots=removed_snapshots, deleted_collections=removed_jobs)
    if removed_snapshots or removed_jobs:
        record('retention.completed', details={'deleted_snapshots': removed_snapshots, 'deleted_collections': removed_jobs})
    return removed_snapshots, removed_jobs
