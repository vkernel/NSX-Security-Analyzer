"""Bounded cleanup of disposable browsing data; source observations are retained."""
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import (Snapshot, RetentionPolicy, Finding, HistoryAssessment,
                     SnapshotComparison, Environment)


def browsing_candidates(environment, policy, now):
    if not policy.browsing_days:
        return environment.snapshots.none()
    cutoff = now - timedelta(days=policy.browsing_days)
    snapshots = environment.snapshots.filter(browsing_status='ready', presentation__isnull=False,
        generated_at__lt=cutoff, created_at__lt=cutoff).filter(
        Q(browsing_refreshed_at__isnull=True) | Q(browsing_refreshed_at__lt=cutoff))
    protected = [pk for testing in (False, True) for pk in environment.snapshots.filter(testing=testing).values_list('pk', flat=True)[:1]]
    protected += list(environment.snapshots.filter(testing=False, imported=False).filter(
        Q(report__inventory_warnings__isnull=True) | Q(report__inventory_warnings=[])).values_list('pk', flat=True)[:1])
    # Paused/current findings may still rely on their last verified snapshot.
    return snapshots.exclude(pk__in=protected).exclude(pk__in=Finding.objects.filter(
        environment=environment, present=True).values('snapshot_id'))


def cleanup_storage(now=None):
    now = now or timezone.now()
    policy = RetentionPolicy.objects.filter(pk=1).first()
    if not policy or not policy.enabled:
        return
    for env_id in Environment.objects.values_list('pk', flat=True):
        with transaction.atomic():
            env = Environment.objects.select_for_update().get(pk=env_id)
            if env.jobs.filter(status__in=['queued', 'running']).exists():
                continue
            snapshot_id = browsing_candidates(env, policy, now).values_list('pk', flat=True).first()
            if snapshot_id:
                snapshot = Snapshot.objects.select_for_update().get(pk=snapshot_id)
                # Recheck after acquiring the snapshot lock; a restore can refresh it.
                if browsing_candidates(env, policy, now).filter(pk=snapshot_id).exists():
                    from .snapshot_index import clear_index
                    clear_index(snapshot_id, keep_coverage=True)
                    Snapshot.objects.filter(pk=snapshot_id).update(browsing_status='archived')
            if policy.clear_legacy_html:
                ids = list(env.snapshots.exclude(html='').values_list('pk', flat=True)[:2])
                # QuerySet.update bypasses source-report invalidation signals.
                Snapshot.objects.filter(pk__in=ids).update(html='')
    if policy.cache_days:
        cutoff = now - timedelta(days=policy.cache_days)
        ids = list(HistoryAssessment.objects.filter(expires_at__lt=cutoff).values_list('pk', flat=True)[:10])
        HistoryAssessment.objects.filter(pk__in=ids).delete()
        # Keep the pair and its audit/task identity. Expire only derived rows;
        # opening the same pair explicitly queues reconstruction.
        for pk in SnapshotComparison.objects.filter(ready=True, status='completed', cache_accessed_at__lt=cutoff).values_list('pk', flat=True)[:2]:
            with transaction.atomic():
                pair = SnapshotComparison.objects.select_for_update().get(pk=pk)
                if pair.status == 'completed' and pair.ready and pair.cache_accessed_at < cutoff:
                    pair.rows.all().delete()
                    pair.ready, pair.status = False, 'expired'
                    pair.save(update_fields=['ready', 'status'])
