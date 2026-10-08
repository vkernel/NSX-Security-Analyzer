from . import page_queries
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
from .findings import LABELS
from .preferences import preferences


@login_required
@require_GET
def comparison(request, pk):
    environment = get_object_or_404(page_queries.environments(), pk=pk)
    latest = list(environment.snapshots.filter(testing=False, imported=False).values_list('pk', flat=True)[:2])
    initial = {'before': latest[1], 'after': latest[0]} if len(latest) == 2 else {}
    data = request.GET if 'before' in request.GET or 'after' in request.GET else None
    form = SnapshotComparisonForm(data, initial=initial, environment=environment, choices_page=request.GET.get("choices_page", 1))
    rows, selected = [], None
    if form.is_bound and form.is_valid():
        from .comparison_cache import comparison_rows
        selected = form.cleaned_data
        rows = comparison_rows(selected['before'], selected['after'])
    page = Paginator(rows, preferences(request).page_size).get_page(request.GET.get('page'))
    query = request.GET.copy()
    if selected:
        query['before'], query['after'] = str(selected['before'].pk), str(selected['after'].pk)
    query.pop('page', None)
    return render(request, 'inventory/comparison.html', {'environment': environment, 'form': form,
        'selected': selected, 'page': page, 'query_string': query.urlencode(), 'count': page.paginator.count})


@login_required
@require_GET
def coverage(request, pk):
    from .coverage import dashboard
    environment = get_object_or_404(page_queries.environments(), pk=pk)
    days = int(request.GET.get('days', '30')) if request.GET.get('days', '30') in ('7', '30', '90') else 30
    result = dashboard(environment, days, lazy=True)
    page = Paginator(result.pop('issues'), preferences(request).page_size).get_page(request.GET.get('page'))
    return render(request, 'inventory/coverage.html', {'environment': environment, 'coverage': result,
        'days': days, 'page': page, 'query_string': f'days={days}', 'failed_count': result['failed'].count(),
        'gap_page': Paginator(result['gaps'], 25).get_page(request.GET.get('gap_page')),
        'jobs': page_queries.jobs(result['failed'])[:20]})


@login_required
@require_GET
def findings(request, pk):
    environment = get_object_or_404(page_queries.environments(), pk=pk)
    # Only findings that met the administration policy enter the review queue.
    rows = environment.findings.filter(present=True, qualification='eligible').select_related('owner').defer('evidence')
    from .forms import FindingFilterForm
    from django.db.models import F
    form = FindingFilterForm(request.GET or {'sort': 'name', 'direction': 'asc'})
    query = request.GET.get('q', '').strip()
    if form.is_valid():
        values = form.cleaned_data
        if values['q']:
            rows = rows.filter(Q(name__icontains=values['q']) | Q(path__icontains=values['q']))
        if values['kind']: rows = rows.filter(kind=values['kind'])
        if values['review']: rows = rows.filter(status=values['review'])
        owner = values['owner']
        if owner == 'none': rows = rows.filter(owner__isnull=True)
        elif owner: rows = rows.filter(owner_id=request.user.pk if owner == 'me' else int(owner))
        order = F(values['sort'] or 'name')
        rows = rows.order_by(order.desc(nulls_last=True) if values['direction'] == 'desc' else order.asc(nulls_last=True), 'pk')
    else:
        rows = rows.none()
    page = Paginator(rows, preferences(request).page_size).get_page(request.GET.get('page'))
    for row in page:
        row.label = LABELS.get(row.kind, row.kind)
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'inventory/findings.html', {'environment': environment, 'page': page,
        'query': query, 'filter_form': form, 'query_string': params.urlencode(),
        'recalculation': environment.finding_recalculation if hasattr(environment, 'finding_recalculation') else None})


@login_required
@require_http_methods(['GET', 'POST'])
def finding_detail(request, pk, finding_id):
    environment = get_object_or_404(page_queries.environments(), pk=pk)
    if request.method == 'POST' and not request.user.is_staff:
        return HttpResponseForbidden('Staff access is required to review findings.')
    from contextlib import nullcontext
    with transaction.atomic() if request.method == 'POST' else nullcontext():
        rows = Finding.objects.select_related('owner').defer('evidence')
        if request.method == 'POST':
            Environment.objects.select_for_update().only('pk').get(pk=pk)
            rows = rows.select_for_update(of=('self',))
        finding = get_object_or_404(rows, pk=finding_id, environment=environment)
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
                from .audit_events import record
                record('finding.reviewed', 'Finding', finding.pk, details={'status': finding.status,
                       'owner_id': finding.owner_id, 'review_date': str(finding.review_date or '')})
                message = f'Status: {finding.get_status_display()}; owner: {finding.owner or "Unassigned"}; review date: {finding.review_date or "Not set"}.'
                if data['note']:
                    message += '\n' + data['note']
                FindingEvent.objects.create(finding=finding, actor=request.user, message=message)
                messages.success(request, 'Review saved. Acknowledgement does not change audit evidence or coverage.')
                return redirect('finding-detail', pk=pk, finding_id=finding.pk)
    page = Paginator(finding.events.select_related('actor'), 20).get_page(request.GET.get('page'))
    return render(request, 'inventory/finding_detail.html', {'environment': environment, 'finding': finding,
        'label': LABELS.get(finding.kind, finding.kind), 'form': form, 'page': page,
        'evidence': json.dumps(finding.evidence, indent=2, ensure_ascii=False) if request.GET.get('evidence') == '1' else None})


@login_required
@require_http_methods(['GET', 'POST'])
def finding_policy(request):
    from .forms import FindingPolicyForm
    from .models import FindingPolicy, FindingRecalculation
    from .observation import policy_for
    if not request.user.is_superuser:
        return HttpResponseForbidden('Administrator access is required.')
    selected = request.GET.get('environment', '')
    environment = get_object_or_404(Environment, pk=selected) if selected else None
    scope = str(environment.pk) if environment else 'global'
    saved = FindingPolicy.objects.filter(scope=scope).first()
    inherited = policy_for(environment) if environment else FindingPolicy.objects.filter(scope='global').first() or FindingPolicy()
    previous = {name: getattr(inherited, name) for name in FindingPolicyForm.Meta.fields}
    form = FindingPolicyForm(request.POST or None, instance=saved or FindingPolicy(),
        initial={name: getattr(inherited, name) for name in FindingPolicyForm.Meta.fields})
    if request.method == 'POST':
        if request.POST.get('inherit') == '1' and environment:
            from .finding_recalculation import queue
            from .observation import KINDS
            with transaction.atomic():
                FindingPolicy.objects.filter(scope=scope).delete()
                queue([environment.pk], KINDS)
            return redirect(request.get_full_path())
        if form.is_valid():
            obj = form.save(commit=False)
            obj.scope, obj.environment = scope, environment
            from .finding_recalculation import queue
            from .observation import KINDS
            kinds = [kind for kind in KINDS if previous[kind+'_days'] != getattr(obj, kind+'_days')]
            if any(previous[field] != getattr(obj, field) for field in ('minimum_observations', 'maximum_gap_hours')) or not kinds:
                kinds = list(KINDS)
            with transaction.atomic():
                obj.save()
                affected = [environment.pk] if environment else Environment.objects.exclude(pk__in=FindingPolicy.objects.filter(environment__isnull=False).values('environment_id')).values_list('pk', flat=True)
                queue(affected, kinds)
            from django.contrib import messages
            messages.success(request, 'Criteria saved. Finding reviews are being recalculated from retained collection history by the worker; no new collection is required.')
            return redirect(request.get_full_path())
    return render(request, 'inventory/finding_policy.html', {'form': form, 'selected': selected,
        'environment': environment, 'overridden': bool(saved), 'environments': Environment.objects.only('pk', 'name').order_by('name'),
        'recalculations': FindingRecalculation.objects.filter(**({'environment': environment} if environment else {})).select_related('environment').defer('environment__password_ciphertext', 'environment__ca_certificate').order_by('-requested_at')[:20]})
