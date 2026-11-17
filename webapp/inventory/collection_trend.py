"""Per-environment collection trend: durations, requests, throttling and evidence gaps.

A compact performance summary is saved in Snapshot.summary when a snapshot is
published, so the trend reads small keys only. Older snapshots fall back to a few
report keys; the membership diagnostics are never read. Failed collections come
from the job rows (duration and error code). Nothing here contacts NSX.
"""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .models import AuditJob, Environment, Snapshot
from .page_response import render

PHASES = ('inventory', 'search', 'dfw', 'membership', 'references', 'tags', 'network_membership', 'network_collection')
LIMIT = 30


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def performance_summary(report):
    """Compact figures saved with each snapshot for the collection trend."""
    performance = report.get('performance') or {}
    http, paced = performance.get('http') or {}, performance.get('request_pacing') or {}
    concurrency = performance.get('concurrency') or {}
    phases = performance.get('phases_seconds') or {}
    dfw = report.get('dfw') or {}
    statistics = dfw.get('collection_diagnostics') or {}
    search = report.get('search_coverage') or {}
    return {'elapsed_seconds': number(performance.get('elapsed_seconds')),
            'phases': {key: phases[key] for key in PHASES if number(phases.get(key)) is not None},
            'requests': number(http.get('requests')), 'retries': number(http.get('retries')),
            'throttled': number(paced.get('throttled_attempts')),
            'concurrency_final': number(concurrency.get('final')), 'concurrency_peak': number(concurrency.get('peak')),
            'fallback_rules': number(statistics.get('fallback_rules')),
            'unknown_membership': sum(row.get('membership') == 'unknown' for row in report.get('objects', []) if isinstance(row, dict)),
            'unknown_statistics': sum(row.get('hit_status') == 'unknown' for row in dfw.get('rules', []) if isinstance(row, dict)),
            'search_incomplete': bool(search.get('failed') or search.get('errors'))}


LEGACY_KEYS = ('report__performance__elapsed_seconds', 'report__performance__phases_seconds', 'report__performance__http',
        'report__performance__request_pacing__throttled_attempts', 'report__performance__concurrency__final',
        'report__performance__concurrency__peak', 'report__dfw__collection_diagnostics__fallback_rules')


def legacy_summaries(pks):
    """Older snapshots, in one query: only small performance keys, never membership diagnostics."""
    return {row['pk']: legacy_summary(row) for row in Snapshot.objects.filter(pk__in=pks).values('pk', *LEGACY_KEYS)} if pks else {}


def legacy_summary(row):
    phases = row['report__performance__phases_seconds'] or {}
    http = row['report__performance__http'] or {}
    return {'elapsed_seconds': number(row['report__performance__elapsed_seconds']),
            'phases': {key: phases[key] for key in PHASES if number(phases.get(key)) is not None},
            'requests': number(http.get('requests')), 'retries': number(http.get('retries')),
            'throttled': number(row['report__performance__request_pacing__throttled_attempts']),
            'concurrency_final': number(row['report__performance__concurrency__final']),
            'concurrency_peak': number(row['report__performance__concurrency__peak']),
            'fallback_rules': number(row['report__dfw__collection_diagnostics__fallback_rules']),
            'unknown_membership': None, 'unknown_statistics': None, 'search_incomplete': None}


def rows(environment):
    jobs = list(AuditJob.objects.filter(environment=environment, testing=False, status__in=('succeeded', 'failed'))
                .order_by('-created_at').only('id', 'status', 'created_at', 'started_at', 'finished_at', 'scheduled', 'retry_of_id',
                                              'worker_slot')[:LIMIT])
    snapshots = {row['job_id']: row for row in Snapshot.objects.filter(job_id__in=[job.pk for job in jobs])
                 .values('job_id', 'pk', 'needs_review', 'summary__performance')}
    legacy = legacy_summaries([row['pk'] for row in snapshots.values() if not isinstance(row['summary__performance'], dict)])
    codes = dict(AuditJob.objects.filter(pk__in=[job.pk for job in jobs if job.status == 'failed'])
                 .values_list('pk', 'diagnostics__error__code'))
    result = []
    for job in jobs:
        saved = snapshots.get(job.pk)
        figures = None
        if saved:
            figures = saved['summary__performance'] if isinstance(saved['summary__performance'], dict) else legacy.get(saved['pk'])
        duration = (job.finished_at - job.started_at).total_seconds() if job.started_at and job.finished_at else None
        result.append({'job': job, 'snapshot_id': saved['pk'] if saved else None, 'needs_review': saved['needs_review'] if saved else None,
                       'duration': duration, 'figures': figures or {}, 'error_code': codes.get(job.pk) or ''})
    return result


def duration_label(seconds):
    if seconds is None:
        return '—'
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return (f'{hours} h {minutes} min' if hours else f'{minutes} min {seconds} s' if minutes else f'{seconds} s')


def overview(entries):
    durations = sorted(entry['duration'] for entry in entries if entry['duration'] is not None and entry['job'].status == 'succeeded')
    median = durations[len(durations) // 2] if durations else None
    failed = sum(entry['job'].status == 'failed' for entry in entries)
    throttled = sum(bool(entry['figures'].get('throttled')) for entry in entries)
    longest = max(durations) if durations else None
    for entry in entries:
        entry['duration_label'] = duration_label(entry['duration'])
        entry['bar'] = round(100 * entry['duration'] / longest) if longest and entry['duration'] else 0
        phases = entry['figures'].get('phases') or {}
        entry['slowest_phase'] = max(phases, key=phases.get).replace('_', ' ') if phases else ''
    return {'count': len(entries), 'median': duration_label(median), 'longest': duration_label(longest),
            'failed': failed, 'throttled': throttled}


@login_required
@require_GET
def collection_trend(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden('Staff access is required to view collection performance.')
    environment = get_object_or_404(Environment.objects.only('id', 'name'), pk=pk)
    entries = rows(environment)
    return render(request, 'inventory/collection_trend.html', {'environment': environment, 'entries': entries,
                                                               'overview': overview(entries)})
