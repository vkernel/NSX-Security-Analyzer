"""Environment-level history, coverage and review pages."""
import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from .models import Environment, Finding, FindingEvent
from .forms import SnapshotComparisonForm, FindingReviewForm
from .findings import synchronize, LABELS
from .preferences import preferences


@login_required
@require_GET
def comparison(request, pk):
    environment = get_object_or_404(Environment, pk=pk)
    latest = list(environment.snapshots.filter(testing=False, imported=False).values_list('pk', flat=True)[:2])
    data = request.GET if 'before' in request.GET or 'after' in request.GET else ({'before': latest[1], 'after': latest[0]} if len(latest) == 2 else None)
    form = SnapshotComparisonForm(data, environment=environment)
    rows, selected = [], None
    if form.is_bound and form.is_valid():
        from .comparison import compare
        selected = form.cleaned_data
        rows = compare(selected['before'].report, selected['after'].report)
    page = Paginator(rows, preferences(request).page_size).get_page(request.GET.get('page'))
    query = request.GET.copy()
    if selected:
        query['before'], query['after'] = str(selected['before'].pk), str(selected['after'].pk)
    query.pop('page', None)
    return render(request, 'inventory/comparison.html', {'environment': environment, 'form': form,
        'selected': selected, 'page': page, 'query_string': query.urlencode(), 'count': len(rows)})


@login_required
@require_GET
def coverage(request, pk):
    from .coverage import dashboard
    environment = get_object_or_404(Environment, pk=pk)
    days = int(request.GET.get('days', '30')) if request.GET.get('days', '30') in ('7', '30', '90') else 30
    result = dashboard(environment, days)
    page = Paginator(result.pop('issues'), preferences(request).page_size).get_page(request.GET.get('page'))
    return render(request, 'inventory/coverage.html', {'environment': environment, 'coverage': result,
        'days': days, 'page': page, 'query_string': f'days={days}', 'failed_count': result['failed'].count(),
        'gap_page': Paginator(result['gaps'], 25).get_page(request.GET.get('gap_page')),
        'jobs': result['failed'].select_related('environment', 'snapshot').defer('config', 'snapshot__report', 'snapshot__html')[:20]})


@login_required
@require_GET
def findings(request, pk):
    environment = get_object_or_404(Environment, pk=pk)
    synchronize(environment.pk)  # Establish a baseline for pre-upgrade snapshots too.
    rows = environment.findings.select_related('owner')
    state = request.GET.get('status', 'present')
    if state == 'present':
        rows = rows.filter(present=True)
    elif state in ('open', 'acknowledged'):
        rows = rows.filter(present=True, status=state)
    elif state == 'absent':
        rows = rows.filter(present=False)
    elif state == 'due':
        rows = rows.filter(present=True, review_date__lte=timezone.localdate())
    query = request.GET.get('q', '').strip()
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(path__icontains=query))
    page = Paginator(rows, preferences(request).page_size).get_page(request.GET.get('page'))
    for row in page:
        row.label = LABELS.get(row.kind, row.kind)
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'inventory/findings.html', {'environment': environment, 'page': page,
        'state': state, 'query': query, 'query_string': params.urlencode()})


@login_required
@require_http_methods(['GET', 'POST'])
def finding_detail(request, pk, finding_id):
    environment = get_object_or_404(Environment, pk=pk)
    if request.method == 'POST' and not request.user.is_staff:
        return HttpResponseForbidden('Staff access is required to review findings.')
    synchronize(environment.pk)
    with transaction.atomic():
        Environment.objects.select_for_update().get(pk=pk)
        finding = get_object_or_404(Finding.objects.select_for_update(), pk=finding_id, environment=environment)
        form = FindingReviewForm(request.POST if request.method == 'POST' else None,
            initial={'status': finding.status, 'owner': finding.owner_id, 'review_date': finding.review_date, 'revision': finding.revision})
        if request.method == 'POST' and form.is_valid():
            data = form.cleaned_data
            if data['revision'] != finding.revision:
                form.add_error(None, 'This finding changed while you were reviewing it. Reload the page before saving.')
            else:
                finding.owner, finding.status, finding.review_date = data['owner'], data['status'], data['review_date']
                finding.revision += 1
                finding.save()
                message = f'Status: {finding.get_status_display()}; owner: {finding.owner or "Unassigned"}; review date: {finding.review_date or "Not set"}.'
                if data['note']:
                    message += '\n' + data['note']
                FindingEvent.objects.create(finding=finding, actor=request.user, message=message)
                messages.success(request, 'Review saved. Acknowledgement does not change audit evidence or coverage.')
                return redirect('finding-detail', pk=pk, finding_id=finding.pk)
    page = Paginator(finding.events.select_related('actor'), 20).get_page(request.GET.get('page'))
    return render(request, 'inventory/finding_detail.html', {'environment': environment, 'finding': finding,
        'label': LABELS.get(finding.kind, finding.kind), 'form': form, 'page': page,
        'evidence': json.dumps(finding.evidence, indent=2, ensure_ascii=False)})
