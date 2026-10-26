"""Read-only overview of independent background queues."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET
from . import activity_feed
from django.core.paginator import Paginator
from .models import AuditJob, Environment, FindingRecalculation, SnapshotComparison, Snapshot


@login_required
@require_GET
def activity(request):
    selected = request.GET.get('environment', '')
    kind = request.GET.get('type', '')
    state = request.GET.get('status', '')
    tab = request.GET.get('tab', 'all')
    if tab not in ('all', 'running', 'audit'): tab = 'all'
    options = activity_feed.filters(request)
    if tab == 'running': state = 'active'
    tasks = []
    def limited(query, time_field):
        query = activity_feed.dated(query, options, time_field)
        if tab == 'audit' or options['actor'] or options['outcome']:
            return []
        if state == 'active':
            return query.filter(status__in=['queued', 'running']).order_by(time_field)[:60]
        if state == 'failed':
            return query.filter(status='failed').order_by('-' + time_field)[:60]
        # Keep active work visible even when there are many recent completions.
        return list(query.filter(status__in=['queued', 'running']).order_by(time_field)[:30]) + list(query.exclude(status__in=['queued', 'running']).order_by('-' + time_field)[:30])
    if kind in ('', 'collection'):
        rows = AuditJob.objects.select_related('environment', 'snapshot').only('id', 'environment__id', 'environment__name', 'snapshot__id', 'status', 'created_at', 'started_at', 'finished_at', 'progress_stage')
        if selected: rows = rows.filter(environment_id=selected) if selected.isdecimal() and len(selected)<19 else rows.none()
        for job in limited(rows, 'created_at'):
            url = reverse('snapshot', args=[job.snapshot.pk]) if hasattr(job, 'snapshot') else reverse('environment', args=[job.environment_id])
            tasks.append(dict(kind='Collection', environment=job.environment.name, status=job.status,
                at=job.created_at, started=job.started_at, finished=job.finished_at, detail=job.progress_stage, url=url))
    if kind in ('', 'review'):
        rows = FindingRecalculation.objects.select_related('environment').only('environment__id', 'environment__name', 'status', 'requested_at', 'started_at', 'finished_at', 'processed', 'total', 'error')
        if selected: rows = rows.filter(environment_id=selected) if selected.isdecimal() and len(selected)<19 else rows.none()
        for job in limited(rows, 'requested_at'):
            tasks.append(dict(kind='Finding review indexing', environment=job.environment.name, status=job.status,
                at=job.requested_at, started=job.started_at, finished=job.finished_at,
                detail=f'{job.processed} of {job.total} processed' if job.status == 'running' else job.error,
                url=reverse('findings', args=[job.environment_id])))
    if kind in ('', 'comparison'):
        rows = SnapshotComparison.objects.select_related('after__environment').only('id', 'before_id', 'after__id', 'after__environment__id', 'after__environment__name', 'status', 'ready', 'error', 'created_at')
        if selected: rows = rows.filter(after__environment_id=selected) if selected.isdecimal() and len(selected)<19 else rows.none()
        for job in limited(rows, 'created_at'):
            tasks.append(dict(kind='Snapshot comparison', environment=job.after.environment.name,
                status='completed' if job.ready else job.status, at=job.created_at,
                detail=job.error, url=reverse('snapshot-comparison', args=[job.after.environment_id])+f'?before={job.before_id}&after={job.after_id}'))
    if options['q']:
        tasks = [task for task in tasks if options['q'].casefold() in (task['environment'] + ' ' + task['detail']).casefold()]
    if kind in ('', 'deletion') and tab != 'audit':
        tasks += activity_feed.deletion_cards(request, options, selected, state == 'active')
    page = None
    if tab == 'audit':
        page = Paginator(activity_feed.audit_rows(request, options, selected, kind), 50).get_page(request.GET.get('page'))
        tasks = activity_feed.audit_cards(page)
    elif tab == 'all' and state != 'active':
        tasks += activity_feed.audit_cards(activity_feed.audit_rows(request, options, selected, kind).filter(action__startswith='finding.').exclude(action__startswith='finding.nsx_')[:100])
    tasks.sort(key=lambda task: task['at'], reverse=True)
    params=request.GET.copy(); params.pop('page', None)
    response = render(request, 'inventory/activity.html', dict(tasks=tasks, environments=Environment.objects.only('id','name'),
        selected=selected, kind=kind, state=state, tab=tab, filters=options, page=page, query_string=params.urlencode(),
        can_delete=activity_feed.permitted(request.user),
        outcome_choices=[('deleted','Deleted'),('blocked','Blocked'),('already_absent','Already absent'),
            ('failed','Failed'),('uncertain','Uncertain'),('verified','Preview verified'),
            ('owner_review','Owner review'),('second_review','Awaiting second approval'),('ready','Ready for decommissioning'),
            ('rejected','Rejected'),('decommissioned','Decommissioned'),('unassigned','Unassigned')]))
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def collection_result(request, pk):
    job = get_object_or_404(AuditJob.objects.only('environment_id'), pk=pk)
    snapshot = Snapshot.objects.filter(job_id=pk).only('pk').first()
    return redirect('snapshot', pk=snapshot.pk) if snapshot else redirect('environment', pk=job.environment_id)
