from . import page_queries
"""Environment-level history, coverage and review pages."""
import json
from urllib.parse import urlencode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from .models import Environment, Finding, FindingEvent, DeletionItem
from .forms import SnapshotComparisonForm, FindingReviewForm, BulkAssignmentForm
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
    term = request.GET.get('q', '').strip()[:255]
    change = request.GET.get('change', '')
    change_choices = ['Added', 'Removed', 'Changed', 'Presence uncertain', 'Evidence incomplete']
    counts = []
    rows, selected, comparison_job = [], None, None
    if form.is_bound and form.is_valid():
        from .models import SnapshotComparison
        selected = form.cleaned_data
        with transaction.atomic():
            comparison_job, _ = SnapshotComparison.objects.get_or_create(before=selected['before'], after=selected['after'])
            comparison_job = SnapshotComparison.objects.select_for_update().get(pk=comparison_job.pk)
            comparison_job.cache_accessed_at = timezone.now()
            if comparison_job.status == 'expired':
                comparison_job.status = 'queued'
            comparison_job.save(update_fields=['cache_accessed_at', 'status'])
        if comparison_job.ready:
            from django.db.models import Count
            rows = comparison_job.rows.all()
            counts = list(rows.values('data__status').annotate(total=Count('pk')).order_by('data__status'))
            if term: rows = rows.filter(Q(data__name__icontains=term) | Q(data__path__icontains=term))
            if change in change_choices: rows = rows.filter(data__status=change)
            elif change: rows = rows.none()
            rows = rows.values_list('data', flat=True)
    page = Paginator(rows, preferences(request).page_size).get_page(request.GET.get('page'))
    for row in page:
        for difference in row.get('changes', []):
            difference['label'] = str(difference.get('field', '')).replace('_', ' ').capitalize()
    query = request.GET.copy()
    if selected:
        query['before'], query['after'] = str(selected['before'].pk), str(selected['after'].pk)
    query.pop('page', None)
    return render(request, 'inventory/comparison.html', {'environment': environment, 'form': form,
        'search': term, 'change': change, 'change_choices': change_choices, 'change_counts': counts,
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


def filtered_findings(request, environment, params):
    rows = environment.findings.filter(Q(present=True, qualification='eligible') | Q(workflow_state__in=['owner_review','second_review','ready','rejected','decommissioned'])).select_related('owner__keycloakidentity', 'owner__ldapidentity').defer('evidence', 'approvals')
    if not params.get('review'):
        rows = rows.exclude(workflow_state__in=['rejected', 'decommissioned'])
    if params.get('review') == 'suppressed':
        rows = environment.findings.filter(qualification='suppressed').select_related('owner__keycloakidentity', 'owner__ldapidentity').defer('evidence', 'approvals')
    else:
        rows = rows.exclude(qualification='suppressed')
    from .forms import FindingFilterForm
    from django.db.models import F
    form = FindingFilterForm(params or {'sort': 'name', 'direction': 'asc'})
    if not request.user.is_staff: form.fields['owner'].widget.attrs.pop('data-user-search', None)
    query = params.get('q', '').strip()
    if form.is_valid():
        values = form.cleaned_data
        if values['q']:
            rows = rows.filter(Q(name__icontains=values['q']) | Q(path__icontains=values['q']))
        if values['kind']: rows = rows.filter(kind=values['kind'])
        if values['review'] and values['review'] != 'suppressed':
            rows = rows.filter(workflow_state=values['review'])
            if values['review'] in ('second_review', 'ready'):
                rows = rows.filter(owner__isnull=False)
        owner = values['owner']
        if owner == 'none': rows = rows.filter(owner__isnull=True)
        elif owner: rows = rows.filter(owner_id=request.user.pk if owner == 'me' else int(owner))
        order = F('workflow_state' if values['sort']=='status' else values['sort'] or 'name')
        rows = rows.order_by(order.desc(nulls_last=True) if values['direction'] == 'desc' else order.asc(nulls_last=True), 'pk')
    else:
        rows = rows.none()
    return rows, form, query


@login_required
@require_http_methods(['GET', 'POST'])
def findings(request, pk):
    environment = get_object_or_404(page_queries.environments(), pk=pk)
    if request.method == 'POST':
        if not request.user.is_staff:
            return HttpResponseForbidden('Operator or administrator access is required.')
        from django.core.exceptions import ValidationError
        from .finding_workflow import decide
        selections = request.POST.getlist('selected')
        reason = request.POST.get('reason', '').strip()
        action = request.POST.get('action')
        owner = None
        if action == 'assign':
            assignment = BulkAssignmentForm(request.POST)
            if not assignment.is_valid():
                messages.error(request, 'Choose an active operator or administrator as owner; no findings were assigned.')
                return redirect(request.get_full_path())
            owner = assignment.cleaned_data['owner']
        if action not in ('assign', 'unassign') or (action == 'unassign' and not reason) or len(reason) > 5000 or not 1 <= len(selections) <= 100:
            messages.error(request, 'Select 1–100 findings. Returning findings to unassigned also requires a reason of at most 5000 characters.')
            return redirect(request.get_full_path())
        released, skipped = 0, []
        with transaction.atomic():
            Environment.objects.select_for_update().only('pk').get(pk=pk)
            for value in dict.fromkeys(selections):
                try:
                    finding_id, revision = (int(part) for part in value.split(':'))
                    if not 0 < finding_id < 2**63 or not 0 <= revision < 2**63: raise ValueError
                except (ValueError, TypeError):
                    skipped.append('Invalid selection; reload the page.')
                    continue
                finding = Finding.objects.select_for_update().filter(pk=finding_id, environment=environment).first()
                if finding is None:
                    skipped.append('Finding no longer available in this environment.')
                elif action == 'assign' and finding.owner_id:
                    skipped.append(f'{finding.name}: already assigned; existing owner retained.')
                elif finding.revision != revision:
                    skipped.append(f'{finding.name}: changed since selection; reload before retrying.')
                else:
                    try:
                        decide(finding, request.user, action, reason, owner=owner)
                    except ValidationError as exc:
                        skipped.append(f'{finding.name}: {"; ".join(exc.messages)}')
                    else:
                        released += 1
        if action == 'assign':
            from .user_labels import user_label
            messages.success(request, f'Assigned {released} finding(s) to {user_label(owner)}; skipped {len(skipped)}.')
        else:
            messages.success(request, f'Returned {released} finding(s) to unassigned; skipped {len(skipped)}.')
        for detail in skipped:
            messages.warning(request, detail)
        return redirect(request.get_full_path())
    # Only findings that met the administration policy enter the review queue.
    from django.db.models import Count
    qualification_counts = list(environment.findings.filter(present=True).values('qualification').annotate(total=Count('pk')).order_by('qualification'))
    historical_finding_count = environment.findings.filter(present=False).count()
    rows, form, query = filtered_findings(request, environment, request.GET)
    page = Paginator(rows, preferences(request).page_size).get_page(request.GET.get('page'))
    from .deletion import KINDS
    from .evidence_coverage import snapshot_issues, relevant
    latest = environment.snapshots.filter(testing=False, imported=False).only('pk', 'summary').first()
    coverage = snapshot_issues(latest) if latest else []
    release_eligible = 0
    for row in page:
        row.label = 'Prolonged empty VDI group' if row.qualification_reason.startswith('Prolonged empty VDI group') else LABELS.get(row.kind, row.kind)
        row.can_assign = bool(request.user.is_staff and not row.owner_id and row.workflow_state != 'decommissioned')
        row.can_release = bool(request.user.is_staff and row.owner_id and row.workflow_state != 'decommissioned'
                               and (row.owner_id == request.user.pk or request.user.is_superuser))
        row.verification_paused = bool(relevant(coverage, row.kind, row.path))
        row.can_delete = bool(request.user.is_superuser and row.owner_id and row.workflow_state == 'ready' and row.kind in KINDS)
        release_eligible += int(row.can_release)
    params = request.GET.copy()
    params.pop('page', None)
    from .cleanup_tasks import tasks as cleanup_tasks
    cleanup_queue = {'second_review': 'second', 'ready': 'ready', 'owner_review': 'assigned'}.get(request.GET.get('review'), request.GET.get('review') or None)
    return render(request, 'inventory/findings.html', {'environment': environment, 'page': page,
        'cleanup_tasks': cleanup_tasks(request.user, environment=environment.pk, query=query, queue=cleanup_queue),
        'release_eligible': release_eligible, 'review_return': request.get_full_path(),
        'show_release_controls': request.user.is_staff,
        'assignment_form': BulkAssignmentForm(initial={'owner': request.user.pk}),
        'filters_active': any(request.GET.get(key, '').strip() for key in ('q', 'kind', 'review', 'owner')),
        'qualification_counts': qualification_counts, 'historical_finding_count': historical_finding_count,
        'query': query, 'filter_form': form, 'query_string': params.urlencode(),
        'recalculation': environment.finding_recalculation if hasattr(environment, 'finding_recalculation') else None})


@login_required
@require_http_methods(['GET', 'POST'])
def finding_detail(request, pk, finding_id):
    environment = get_object_or_404(page_queries.environments(), pk=pk)
    if request.method == 'POST' and not request.user.is_staff:
        return HttpResponseForbidden('Staff access is required to review findings.')
    from .review_navigation import return_url, navigation, navigation_url
    review_return = return_url(request, pk)
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
            elif data['action'] == 'approve' and request.session.get('impact_preview_' + str(finding.pk)) not in (None, str(finding.snapshot_id)):
                form.add_error(None, 'The snapshot changed since your impact preview. Open Impact preview again before approving.')
            else:
                try:
                    decide(finding, request.user, data['action'], data['note'], data['owner'], data['change_ticket'], data['manual_verified'], data['manual_checks'], data['evidence_reference'])
                except ValidationError as exc:
                    from .audit_events import record
                    record('finding.action_denied', 'Finding', finding.pk, outcome='denied', details={'action': data['action'], 'reason': '; '.join(exc.messages)})
                    form.add_error(None, exc)
                else:
                    messages.success(request, 'Workflow action recorded. No NSX configuration was changed.')
                    return redirect(navigation_url(finding, review_return))
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
    can_decide = request.user.is_staff and bool(finding.owner_id) and ((finding.workflow_state == 'owner_review' and finding.owner_id == request.user.pk)
        or (finding.workflow_state == 'second_review' and finding.owner_id != request.user.pk
            and str(finding.approvals.get('owner', {}).get('actor_id', '')) != str(request.user.pk)))
    steps = [('unassigned','Assign owner'), ('owner_review','Owner review'), ('second_review','Independent review'), ('ready','Ready'), ('decommissioned','Decommissioned')]
    # Assignment captures the current policy; preview that policy before a
    # review starts without rewriting saved decisions during a page read.
    required_approvals = finding.required_approvals
    if finding.workflow_state == 'unassigned':
        from .usability import policy
        from .approval_modes import required
        required_approvals = required(finding.kind, policy())
    from .approval_modes import source
    from .usability import policy as current_approval_policy
    approval_source = source(finding.kind, current_approval_policy()) if finding.workflow_state not in ('rejected', 'decommissioned') else 'Recorded review requirement'
    approval_label = dict(Finding._meta.get_field('required_approvals').choices)[required_approvals]
    if required_approvals == 1:
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
        'deletion_records': DeletionItem.objects.filter(finding=finding).exclude(before={}).select_related('batch').order_by('-pk') if request.user.is_superuser else [],
        'impact_supported': '/groups/' in finding.path or '/security-policies/' in finding.path,
        'review_return': review_return, 'review_navigation': navigation(request, environment, finding, review_return),
        'can_unassign': bool(request.user.is_staff and finding.owner_id and finding.workflow_state != 'decommissioned'
                             and (finding.owner_id == request.user.pk or request.user.is_superuser)),
        'required_approvals': required_approvals, 'approval_label': approval_label, 'approval_source': approval_source,
        'label': 'Prolonged empty VDI group' if finding.qualification_reason.startswith('Prolonged empty VDI group') else LABELS.get(finding.kind, finding.kind), 'form': form, 'page': page, 'approval_decisions': decisions, 'readiness': readiness, 'can_decide': can_decide, 'progress': progress, 'facts': facts, 'owner_name': owner_name, 'owner_detail': owner_detail,
        'query_string': urlencode({'return_to': review_return}),
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
    from .approval_modes import preview
    previewed = False
    with transaction.atomic():
        policy = WorkspacePolicy.objects.select_for_update().filter(pk=1).first() or WorkspacePolicy(pk=1)
        form = ReviewApprovalsForm(request.POST if request.method == 'POST' else None, instance=policy)
        if request.method == 'POST' and form.is_valid():
            if request.POST.get('action') == 'preview':
                previewed = True
            else:
                form.save()
                from .finding_workflow import apply_approval_policy
                changed = apply_approval_policy(policy, request.user)
                messages.success(request, f'Approval and execution settings saved. {changed} open reviews require new approval because their approval count changed.')
                return redirect('review-approvals')
    return render(request, 'inventory/review_approvals.html', {'form': form, 'mode_rows': preview(policy), 'previewed': previewed})


@login_required
@require_http_methods(['POST'])
def comparison_retry(request, comparison_id):
    from .models import SnapshotComparison
    from django.urls import reverse
    pair = get_object_or_404(SnapshotComparison, pk=comparison_id)
    SnapshotComparison.objects.filter(pk=pair.pk, status__in=['failed', 'cancelled'], ready=False).update(status='queued', error='')
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
    if queue not in ('assigned', 'second', 'ready', 'unassigned'): queue = 'assigned'
    rows = Finding.objects.exclude(qualification='suppressed').select_related('environment', 'owner__keycloakidentity', 'owner__ldapidentity').defer('evidence', 'approvals')
    if queue == 'unassigned': rows = rows.filter(owner__isnull=True, present=True, qualification='eligible').exclude(workflow_state='decommissioned')
    elif queue == 'assigned': rows = rows.filter(owner=request.user, workflow_state='owner_review')
    elif queue == 'second':
        rows = rows.filter(workflow_state='second_review', required_approvals=2, owner__isnull=False).exclude(owner=request.user).exclude(approvals__owner__actor_id=str(request.user.pk))
    else: rows = rows.filter(workflow_state='ready', owner__isnull=False)
    term = request.GET.get('q', '').strip()[:255]
    if term: rows = rows.filter(Q(name__icontains=term) | Q(environment__name__icontains=term))
    selected_environment = request.GET.get('environment', '')
    if selected_environment:
        rows = rows.filter(environment_id=selected_environment) if selected_environment.isdecimal() and len(selected_environment)<19 else rows.none()
    from django.db.models import OuterRef, Subquery
    assigned = FindingEvent.objects.filter(finding_id=OuterRef('pk'), details__action='assign').order_by('-created_at', '-pk')
    rows = rows.annotate(assigned_at=Subquery(assigned.values('created_at')[:1]))
    sort = request.GET.get('sort', 'oldest')
    ordering = {'oldest': ['assigned_at', 'pk'], 'name': ['name', 'pk'], 'environment': ['environment__name', 'name', 'pk']}.get(sort, ['assigned_at', 'pk'])
    page = Paginator(rows.order_by(*ordering), preferences(request).page_size).get_page(request.GET.get('page'))
    from .finding_workflow import approval_readiness
    from .deletion import permitted, KINDS
    show_deletion_controls = queue == 'ready' and permitted(request.user)
    for finding in page:
        finding.can_release = bool(finding.owner_id and finding.workflow_state != 'decommissioned' and (finding.owner_id == request.user.pk or request.user.is_superuser))
        finding.can_assign = not finding.owner_id and finding.workflow_state != 'decommissioned'
        finding.can_delete = bool(show_deletion_controls and finding.owner_id and finding.kind in KINDS)
        finding.readiness = approval_readiness(finding)
        finding.approval_count = len([key for key in ('owner', 'second') if key in finding.approvals])

    from urllib.parse import urlencode
    from .cleanup_tasks import tasks as cleanup_tasks
    return render(request, 'inventory/my_work.html', {'cleanup_tasks': cleanup_tasks(request.user, environment=selected_environment, query=term, queue=queue), 'page': page, 'show_deletion_controls': show_deletion_controls, 'review_return': request.get_full_path(), 'queue': queue, 'query': term, 'environments': Environment.objects.only('pk','name'), 'work_environment': selected_environment, 'sort': sort, 'query_string': urlencode({'queue': queue, 'q': term, 'environment':selected_environment, 'sort':sort})})


@login_required
@require_http_methods(['GET', 'POST'])
def approval_recovery(request, pk, finding_id):
    if not request.user.is_active or not request.user.is_superuser:
        return HttpResponseForbidden('Administrator access is required.')
    from .approval_recovery import preview, recover
    from django.core.exceptions import ValidationError
    from contextlib import nullcontext
    error = ''
    with transaction.atomic() if request.method == 'POST' else nullcontext():
        environment = get_object_or_404(Environment.objects.select_for_update() if request.method == 'POST' else Environment.objects, pk=pk)
        rows = Finding.objects.select_for_update() if request.method == 'POST' else Finding.objects
        finding = get_object_or_404(rows, environment=environment, pk=finding_id)
        if request.method == 'POST':
            try:
                if request.POST.get('confirm') != 'yes':
                    raise ValidationError('Confirm recovery of the first approval.')
                recover(finding, request.user, request.POST.get('revision'), request.POST.get('event_id'))
            except ValidationError as exc:
                error = '; '.join(exc.messages)
            else:
                messages.success(request, 'Original first approval recovered. Independent second approval is required.')
                return redirect('finding-detail', pk=pk, finding_id=finding_id)
        result = preview(finding)
    return render(request, 'inventory/approval_recovery.html', {'environment': environment, 'finding': finding, 'recovery': result, 'error': error})
