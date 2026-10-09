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
    rows, selected, comparison_job = [], None, None
    if form.is_bound and form.is_valid():
        from .models import SnapshotComparison
        selected = form.cleaned_data
        comparison_job, _ = SnapshotComparison.objects.get_or_create(before=selected['before'], after=selected['after'])
        if comparison_job.ready:
            rows = comparison_job.rows.values_list('data', flat=True)
    page = Paginator(rows, preferences(request).page_size).get_page(request.GET.get('page'))
    query = request.GET.copy()
    if selected:
        query['before'], query['after'] = str(selected['before'].pk), str(selected['after'].pk)
    query.pop('page', None)
    return render(request, 'inventory/comparison.html', {'environment': environment, 'form': form,
        'selected': selected, 'comparison_job': comparison_job, 'page': page, 'query_string': query.urlencode(), 'count': page.paginator.count})


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
    from django.db.models import Count
    qualification_counts = list(environment.findings.values('qualification').annotate(total=Count('pk')).order_by('qualification'))
    rows = environment.findings.filter(Q(present=True, qualification='eligible') | Q(workflow_state__in=['owner_review','second_review','ready','rejected','decommissioned'])).select_related('owner__keycloakidentity', 'owner__ldapidentity').defer('evidence', 'approvals')
    from .forms import FindingFilterForm
    from django.db.models import F
    form = FindingFilterForm(request.GET or {'sort': 'name', 'direction': 'asc'})
    if not request.user.is_staff: form.fields['owner'].widget.attrs.pop('data-user-search', None)
    query = request.GET.get('q', '').strip()
    if form.is_valid():
        values = form.cleaned_data
        if values['q']:
            rows = rows.filter(Q(name__icontains=values['q']) | Q(path__icontains=values['q']))
        if values['kind']: rows = rows.filter(kind=values['kind'])
        if values['review']: rows = rows.filter(workflow_state=values['review'])
        owner = values['owner']
        if owner == 'none': rows = rows.filter(owner__isnull=True)
        elif owner: rows = rows.filter(owner_id=request.user.pk if owner == 'me' else int(owner))
        order = F('workflow_state' if values['sort']=='status' else values['sort'] or 'name')
        rows = rows.order_by(order.desc(nulls_last=True) if values['direction'] == 'desc' else order.asc(nulls_last=True), 'pk')
    else:
        rows = rows.none()
    page = Paginator(rows, preferences(request).page_size).get_page(request.GET.get('page'))
    for row in page:
        row.label = LABELS.get(row.kind, row.kind)
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'inventory/findings.html', {'environment': environment, 'page': page,
        'qualification_counts': qualification_counts, 'query': query, 'filter_form': form, 'query_string': params.urlencode(),
        'recalculation': environment.finding_recalculation if hasattr(environment, 'finding_recalculation') else None})


@login_required
@require_http_methods(['GET', 'POST'])
def finding_detail(request, pk, finding_id):
    environment = get_object_or_404(page_queries.environments(), pk=pk)
    if request.method == 'POST' and not request.user.is_staff:
        return HttpResponseForbidden('Staff access is required to review findings.')
    from contextlib import nullcontext
    with transaction.atomic() if request.method == 'POST' else nullcontext():
        rows = Finding.objects.select_related('owner__keycloakidentity', 'owner__ldapidentity').defer('evidence')
        if request.method == 'POST':
            Environment.objects.select_for_update().only('pk').get(pk=pk)
            rows = rows.select_for_update(of=('self',))
        finding = get_object_or_404(rows, pk=finding_id, environment=environment)
        form = FindingReviewForm(request.POST if request.method == 'POST' else None,
            initial={'owner': finding.owner_id, 'revision': finding.revision}, finding=finding, actor=request.user)
        if request.method == 'POST' and form.is_valid():
            from django.core.exceptions import ValidationError
            from .finding_workflow import decide
            data = form.cleaned_data
            if data['revision'] != finding.revision:
                form.add_error(None, 'This finding changed while you were reviewing it. Reload the page before saving.')
            else:
                try:
                    decide(finding, request.user, data['action'], data['note'], data['owner'], data['change_ticket'], data['manual_verified'], data['manual_checks'], data['evidence_reference'])
                except ValidationError as exc:
                    from .audit_events import record
                    record('finding.action_denied', 'Finding', finding.pk, outcome='denied', details={'action': data['action'], 'reason': '; '.join(exc.messages)})
                    form.add_error(None, exc)
                else:
                    messages.success(request, 'Workflow action recorded. No NSX configuration was changed.')
                    return redirect('finding-detail', pk=pk, finding_id=finding.pk)
    from django.contrib.auth import get_user_model
    from .user_labels import user_label, with_identities
    decisions = {stage: dict(decision) for stage, decision in finding.approvals.items()}
    legacy_ids = [decision.get('actor_id') for decision in decisions.values()
                  if str(decision.get('actor_id', '')).isdigit() and decision.get('actor_name', '').startswith(('keycloak_', 'ldap_'))]
    legacy_users = {str(user.pk): user for user in with_identities(get_user_model().objects.filter(pk__in=legacy_ids))} if legacy_ids else {}
    for decision in decisions.values():
        actor = legacy_users.get(str(decision.get('actor_id')))
        decision['display_name'] = user_label(actor) if actor else decision.get('actor_name', 'Former reviewer')
    from .finding_workflow import approval_readiness
    readiness = approval_readiness(finding)
    can_decide = request.user.is_staff and ((finding.workflow_state == 'owner_review' and finding.owner_id == request.user.pk)
        or (finding.workflow_state == 'second_review' and finding.owner_id != request.user.pk))
    steps = [('unassigned','Assign owner'), ('owner_review','Owner review'), ('second_review','Independent review'), ('ready','Ready'), ('decommissioned','Decommissioned')]
    if finding.required_approvals == 1:
        steps = [(key, label) for key, label in steps if key != 'second_review']
    current_step = next((i for i, (key, _) in enumerate(steps) if key == finding.workflow_state), -1)
    progress = [{'label': label, 'current': i == current_step, 'done': i < current_step} for i, (_, label) in enumerate(steps)]
    facts = [(label, finding.evidence[key]) for key, label in [('usage','Reference status'), ('membership','Membership'), ('hit_status','Rule activity'), ('disabled','Disabled'), ('rule_count','Rule count')] if key in finding.evidence]
    fact_labels = {'unused_candidate': 'No references found within collected scope', 'empty': 'Confirmed empty',
                   'zero_hits': 'Zero recorded hits', 'True': 'Yes', 'False': 'No'}
    facts = [(label, fact_labels.get(str(value), str(value).replace('_', ' '))) for label, value in facts]
    owner_label = user_label(finding.owner)
    owner_name, _, owner_detail = owner_label.partition(' · ')
    page = Paginator(finding.events.select_related('actor__keycloakidentity', 'actor__ldapidentity').defer('details'), 20).get_page(request.GET.get('page'))
    return render(request, 'inventory/finding_detail.html', {'environment': environment, 'finding': finding,
        'label': LABELS.get(finding.kind, finding.kind), 'form': form, 'page': page, 'approval_decisions': decisions, 'readiness': readiness, 'can_decide': can_decide, 'progress': progress, 'facts': facts, 'owner_name': owner_name, 'owner_detail': owner_detail,
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


@login_required
@require_GET
def finding_history_export(request, pk, finding_id):
    """Download immutable decisions, including evidence retained after snapshot cleanup."""
    from django.http import StreamingHttpResponse
    if not request.user.is_staff:
        return HttpResponseForbidden('Operator or administrator access is required.')
    finding = get_object_or_404(Finding.objects.only('pk'), pk=finding_id, environment_id=pk)
    def content():
        yield '['
        first = True
        for row in finding.events.order_by('created_at', 'pk').values('pk','created_at','actor_id','actor_label','message','details').iterator(chunk_size=100):
            if not first: yield ','
            first = False
            row['created_at'] = row['created_at'].isoformat()
            yield json.dumps(row, ensure_ascii=False)
        yield ']'
    from .audit_events import record
    record('finding.history_exported','Finding',finding.pk)
    response = StreamingHttpResponse(content(), content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="finding-{finding.pk}-history.json"'
    return response


@login_required
@require_http_methods(['GET', 'POST'])
def review_approvals(request):
    from .models import WorkspacePolicy
    from .forms import ReviewApprovalsForm
    if not request.user.is_superuser:
        return HttpResponseForbidden('Administrator access is required.')
    with transaction.atomic():
        policy = WorkspacePolicy.objects.select_for_update().filter(pk=1).first() or WorkspacePolicy(pk=1)
        form = ReviewApprovalsForm(request.POST if request.method == 'POST' else None, instance=policy)
        if request.method == 'POST' and form.is_valid():
            form.save()
            messages.success(request, 'Approval requirement saved. Existing reviews keep their assigned requirement.')
            return redirect('review-approvals')
    return render(request, 'inventory/review_approvals.html', {'form': form})


@login_required
@require_http_methods(['POST'])
def comparison_retry(request, comparison_id):
    from .models import SnapshotComparison
    from django.urls import reverse
    pair = get_object_or_404(SnapshotComparison, pk=comparison_id)
    SnapshotComparison.objects.filter(pk=pair.pk, status='failed', ready=False).update(status='queued', error='')
    return redirect(reverse('snapshot-comparison', args=[pair.after.environment_id]) + f'?before={pair.before_id}&after={pair.after_id}')


@login_required
@require_GET
def reviewer_search(request):
    from django.contrib.auth import get_user_model
    from django.http import JsonResponse
    from .user_labels import user_label, with_identities
    if not request.user.is_staff:
        return HttpResponseForbidden('Operator access required.')
    term = request.GET.get('q', '').strip()[:100]
    rows = get_user_model().objects.filter(is_active=True, is_staff=True)
    for word in term.split():
        rows = rows.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word) | Q(email__icontains=word) | Q(username__icontains=word))
    rows = list(with_identities(rows.order_by('first_name', 'last_name', 'username'))[:21])
    response = JsonResponse({'items': [{'id': u.pk, 'label': user_label(u)} for u in rows[:20]], 'has_more': len(rows) > 20})
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def my_work(request):
    if not request.user.is_staff:
        return HttpResponseForbidden('Operator access required.')
    queue = request.GET.get('queue', 'assigned')
    if queue not in ('assigned', 'second', 'ready'): queue = 'assigned'
    rows = Finding.objects.select_related('environment', 'owner__keycloakidentity', 'owner__ldapidentity').defer('evidence', 'approvals')
    if queue == 'assigned': rows = rows.filter(owner=request.user, workflow_state='owner_review')
    elif queue == 'second':
        rows = rows.filter(workflow_state='second_review', required_approvals=2).exclude(owner=request.user).exclude(approvals__owner__actor_id=str(request.user.pk))
    else: rows = rows.filter(workflow_state='ready')
    term = request.GET.get('q', '').strip()[:255]
    if term: rows = rows.filter(Q(name__icontains=term) | Q(environment__name__icontains=term))
    page = Paginator(rows.order_by('last_seen', 'pk'), preferences(request).page_size).get_page(request.GET.get('page'))
    from urllib.parse import urlencode
    return render(request, 'inventory/my_work.html', {'page': page, 'queue': queue, 'query': term, 'query_string': urlencode({'queue': queue, 'q': term})})
