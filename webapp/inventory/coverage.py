"""Collection visibility, using prepared coverage rows for web requests."""
from datetime import timedelta
from django.utils import timezone
from .usability import freshness, policy
from .models import SnapshotCoverage
from .coverage_index import PreparedIssues, report_issues


def dashboard(environment, days, now=None, lazy=False):
    now = now or timezone.now()
    start = now - timedelta(days=days)
    snapshots = environment.snapshots.filter(testing=False, imported=False)
    stamps = list(snapshots.filter(generated_at__gte=start, generated_at__lte=now).order_by('generated_at').values_list('generated_at', flat=True))
    threshold = timedelta(minutes=environment.sync_interval_minutes * 2) if environment.sync_interval_minutes else timedelta(hours=24)
    boundaries = [start] + stamps + [now]
    gaps = [{'start': a, 'end': b, 'hours': round((b-a).total_seconds()/3600, 2)}
            for a, b in zip(boundaries, boundaries[1:]) if b-a > threshold]
    latest = snapshots.filter(generated_at__lte=now).only('id','generated_at','needs_review').first()
    saved = SnapshotCoverage.objects.filter(snapshot_id=latest.pk).first() if latest else None
    pending = bool(lazy and latest and saved is None)
    if saved:
        issues = PreparedIssues(saved) if lazy else list(saved.issues.values('area','name','detail'))
    elif pending:
        issues = [{'area':'Audit', 'name':'Coverage summary not prepared',
                   'detail':'Collect a new snapshot or ask an administrator to prepare saved coverage. Detailed checks have not been loaded.'}]
    elif latest:
        # Explicit non-web callers can inspect legacy evidence. Web requests never
        # silently fall back to parsing report JSON or rebuilding an index.
        issues = list(report_issues(latest.report, latest.needs_review))
    else:
        issues = []
    options = policy()
    return {'start': start, 'end': now, 'latest': latest, 'issues': issues, 'gaps': gaps,
            'snapshots': len(stamps), 'first': stamps[0] if stamps else None, 'last': stamps[-1] if stamps else None,
            'threshold_hours': round(threshold.total_seconds()/3600, 2), 'freshness': freshness(environment, options),
            'stale_data': latest is None or now-latest.generated_at > timedelta(hours=options.stale_hours),
            'failed': environment.jobs.filter(status='failed', created_at__gte=start, created_at__lte=now),
            'coverage_pending': pending,
            'active': environment.jobs.filter(status__in=['queued', 'running']).count()}
