"""Age-based cleanup, serialized with collectors and persisted in PostgreSQL."""
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import Environment, RetentionPolicy

BATCH_SIZE = 1000


def eligible(environment, policy, now):
    snapshots = environment.snapshots.all()
    # Always preserve the latest full snapshot and the latest testing snapshot.
    protected = [pk for testing in (False, True) for pk in
                 snapshots.filter(testing=testing).values_list('pk', flat=True)[:1]]
    condition = Q(pk__in=[])
    for testing, days in ((False, policy.snapshot_days), (True, policy.testing_days)):
        if days:
            cutoff = now - timedelta(days=days)
            condition |= Q(testing=testing, generated_at__lt=cutoff, created_at__lt=cutoff)
    expired = snapshots.filter(condition).exclude(pk__in=protected)
    jobs = environment.jobs.none()
    if policy.collection_days:
        cutoff = now - timedelta(days=policy.collection_days)
        jobs = environment.jobs.filter(status__in=['succeeded', 'failed'],
            created_at__lt=cutoff, finished_at__lt=cutoff).filter(
                Q(snapshot__isnull=True) | Q(snapshot__pk__in=expired.values('pk')))
    return expired, jobs


def preview(policy, now=None):
    now = now or timezone.now()
    rows = []
    for environment in Environment.objects.all():
        busy = environment.jobs.filter(status__in=['queued', 'running']).exists()
        snapshots, jobs = eligible(environment, policy, now)
        rows.append({'environment': environment, 'busy': busy,
                     'snapshots': 0 if busy else snapshots.count(), 'collections': 0 if busy else jobs.count()})
    return rows


def cleanup_retention(now=None):
    now = now or timezone.now()
    RetentionPolicy.objects.get_or_create(pk=1)
    with transaction.atomic():
        policy = RetentionPolicy.objects.select_for_update().get(pk=1)
        if not policy.enabled or (policy.last_run and now-policy.last_run < timedelta(hours=1)):
            return (0, 0)
        # Defend against invalid direct database changes as well as form input.
        if any(days and days < 91 for days in (policy.snapshot_days, policy.collection_days)):
            return (0, 0)
        if policy.testing_days and policy.testing_days < 1:
            return (0, 0)
        removed_snapshots = removed_jobs = 0
        for pk in Environment.objects.values_list('pk', flat=True):
            environment = Environment.objects.select_for_update().get(pk=pk)
            if environment.jobs.filter(status__in=['queued', 'running']).exists():
                continue
            expired, _ = eligible(environment, policy, now)
            ids = list(expired.values_list('pk', flat=True)[:BATCH_SIZE])
            if ids:
                removed_snapshots += environment.snapshots.filter(pk__in=ids).delete()[0]
            _, jobs = eligible(environment, policy, now)
            # Retained snapshots protect their source jobs, even during a backlog.
            ids = list(jobs.filter(snapshot__isnull=True).values_list('pk', flat=True)[:BATCH_SIZE])
            if ids:
                removed_jobs += environment.jobs.filter(pk__in=ids).delete()[0]
        policy.last_run = now
        policy.deleted_snapshots = removed_snapshots
        policy.deleted_collections = removed_jobs
        policy.save(update_fields=['last_run', 'deleted_snapshots', 'deleted_collections'])
        return removed_snapshots, removed_jobs
