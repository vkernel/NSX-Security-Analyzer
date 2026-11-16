"""One collection: progress phases, saved pacing figures, coverage and recent history.

Reads the job row, small keys of its published snapshot and the coverage index.
It never contacts NSX and never shows collection configuration beyond the
concurrency limits (the saved configuration also holds encrypted credentials).
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .models import AuditJob, Snapshot
from .overview_summary import coverage as snapshot_coverage
from .page_response import render

# The collector reports these numbered phases (services.update_progress / collector progress()).
PHASES = ('Domains, groups and services', 'References, counters and VM associations', 'Firewall rules and counters',
          'VM associations and group membership', 'Tags, network members and interface attachments',
          'Report and hit history', 'Publish snapshot')


def phases(job):
    done = 7 if job.status == 'succeeded' else min(job.progress_completed, 6)
    stopped = job.status in ('failed', 'cancelled')
    result = []
    for index, label in enumerate(PHASES):
        state = ('done' if index < done else
                 'stopped' if stopped and index == done else
                 'skipped' if stopped else
                 'current' if job.status == 'running' and index == done else 'waiting')
        result.append({'label': label, 'state': state, 'number': index + 1})
    return result


def pacing(job, snapshot):
    config = job.config if isinstance(job.config, dict) else {}
    # Only the two pacing limits are read from the saved configuration.
    limits = {'workers': config.get('workers'), 'concurrency': config.get('max_concurrent_requests')}
    if snapshot is None:
        return {'recorded': False, 'limits': limits}
    performance = Snapshot.objects.filter(pk=snapshot.pk).values_list('report__performance', flat=True).get() or {}
    http = performance.get('http') or {}
    paced = performance.get('request_pacing') or {}
    number = lambda value: value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    phase_seconds = performance.get('phases_seconds') or {}
    return {'recorded': bool(performance), 'limits': limits, 'requests': number(http.get('requests')),
            'retries': number(http.get('retries')), 'throttled': number(paced.get('throttled_attempts')),
            'rate': number(paced.get('average_requests_per_second')), 'pacing_wait': number(paced.get('aggregate_wait_seconds')),
            'elapsed': number(performance.get('elapsed_seconds')),
            'phases': sorted(((key.replace('_', ' ').capitalize(), value) for key, value in phase_seconds.items()
                              if number(value) is not None), key=lambda item: -item[1])[:6]}


def history(job):
    rows = list(AuditJob.objects.filter(environment_id=job.environment_id)
                .only('id', 'status', 'created_at', 'started_at', 'finished_at', 'scheduled', 'testing')[:10])
    snapshots = {row['job_id']: row for row in Snapshot.objects.filter(job_id__in=[row.pk for row in rows])
                 .values('job_id', 'pk', 'needs_review', 'report__performance__http__requests')}
    for row in rows:
        saved = snapshots.get(row.pk)
        row.snapshot_id = saved['pk'] if saved else None
        row.coverage = ('Needs review' if saved['needs_review'] else 'Complete') if saved else (
            'Previous snapshot kept' if row.status in ('failed', 'cancelled') else '—')
        row.requests = saved['report__performance__http__requests'] if saved else None
        row.duration = (row.finished_at - row.started_at) if row.started_at and row.finished_at else None
    return rows


@login_required
@require_GET
def collection_detail(request, pk):
    job = get_object_or_404(AuditJob.objects.select_related('environment', 'requested_by').defer(
        'environment__password_ciphertext', 'environment__ca_certificate', 'diagnostics'), pk=pk)
    snapshot = Snapshot.objects.filter(job_id=job.pk).only('pk', 'needs_review', 'generated_at', 'environment_id').first()
    context = {'job': job, 'environment': job.environment, 'snapshot': snapshot, 'progress': job.progress,
               'phases': phases(job), 'pacing': pacing(job, snapshot), 'active': job.status in ('queued', 'running'),
               'coverage': snapshot_coverage(snapshot) if snapshot else None, 'history': history(job)}
    return render(request, 'inventory/collection_detail.html', context)
