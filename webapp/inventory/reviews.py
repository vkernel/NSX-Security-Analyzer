"""One review workspace, shared by personal and environment entry points."""
from types import SimpleNamespace
from urllib.parse import urlencode

from django import forms
from django.db.models import F, Value, CharField, OuterRef, Subquery, Count
from django.db.models.functions import Coalesce, Greatest
from django.core.paginator import Paginator
from django.urls import reverse

from .models import Finding, FindingEvent, DeletionItem, Environment
from .forms import FindingFilterForm
from .preferences import table_page_size
from .page_response import render

QUEUES = [('assigned', 'Assigned to me'), ('second', 'Second approval'),
          ('unassigned', 'Unassigned'), ('ready', 'Ready'), ('all', 'All findings')]
FIELDS = ('task_kind', 'task_id', 'task_name', 'task_environment', 'task_stage', 'task_updated', 'task_assigned')


class ReviewFilterForm(FindingFilterForm):
    task_type = forms.ChoiceField(required=False, label='Task type', choices=[('', 'All task types'),
        ('finding', 'Finding reviews'), ('cleanup', 'Firewall reference cleanup')])
    sort = forms.ChoiceField(required=False, choices=[('oldest', 'Oldest assignment'),
        ('updated', 'Last updated'), ('name', 'Name'), ('environment', 'Environment'),
        ('status', 'Review stage'), ('kind', 'Finding type')])


def queue_for(request, *, legacy=False):
    value = request.GET.get('queue')
    if value in dict(QUEUES):
        return value
    if legacy:
        stage, owner = request.GET.get('review'), request.GET.get('owner')
        if stage == 'second_review': return 'second'
        if stage == 'ready': return 'ready'
        if owner == 'me' and not stage: return 'assigned'
        if owner == 'none' and not stage: return 'unassigned'
    return 'all' if legacy or not request.user.is_staff else 'assigned'


def in_queue(rows, user, queue):
    if queue == 'assigned': return rows.filter(owner=user, workflow_state='owner_review')
    if queue == 'second':
        return rows.filter(workflow_state='second_review', required_approvals=2, owner__isnull=False).exclude(
            owner=user).exclude(approvals__owner__actor_id=str(user.pk))
    if queue == 'unassigned':
        return rows.filter(owner__isnull=True, present=True, qualification='eligible').exclude(workflow_state='decommissioned')
    if queue == 'ready': return rows.filter(workflow_state='ready', owner__isnull=False)
    return rows


def cleanup_in_queue(task, user, queue):
    if queue == 'assigned': return task['item'].batch.requested_by_id == user.pk
    if queue == 'second': return task['second']
    if queue == 'unassigned': return False
    if queue == 'ready': return task['eligible']
    return True


def links(request, queue, environment, counts):
    result = []
    for value, label in QUEUES:
        if not request.user.is_staff and value in ('assigned', 'second'): continue
        params = request.GET.copy()
        for key in ('page', 'owner', 'review'): params.pop(key, None)
        params['queue'] = value
        if environment: params['environment'] = environment.pk
        result.append({'key': value, 'label': label, 'count': counts[value], 'active': value == queue,
                       'url': reverse('my-work') + '?' + params.urlencode()})
    return result


def findings(request, environment, params, queue):
    from .history_views import filtered_findings
    values = params.copy()
    # View selection owns these restrictions; old queue filters must not turn a
    # different queue into a contradictory empty result.
    if queue != 'all':
        for key in ('owner', 'review'): values.pop(key, None)
    values['sort'] = 'name'
    values['direction'] = 'asc'
    rows, _, _ = filtered_findings(request, environment, values, include_history=queue=='all')
    return in_queue(rows, request.user, queue)


def projection(rows, *, cleanup=False):
    if cleanup:
        return rows.order_by().annotate(task_kind=Value('cleanup', output_field=CharField()),
            task_id=F('pk'), task_name=F('name'), task_environment=F('batch__environment__name'),
            task_stage=F('status'), task_updated=Coalesce('finished_at', 'verified_at', 'batch__checked_at', 'batch__created_at'),
            task_assigned=F('batch__created_at')).values(*FIELDS)
    event = FindingEvent.objects.filter(finding_id=OuterRef('pk')).order_by('-created_at', '-pk')
    assigned = event.filter(details__action='assign')
    return rows.order_by().annotate(task_kind=Value('finding', output_field=CharField()),
        task_id=F('pk'), task_name=F('name'), task_environment=F('environment__name'), task_stage=F('workflow_state'),
        task_updated=Greatest('evaluated_at', Coalesce(Subquery(event.values('created_at')[:1]), 'last_seen')),
        task_assigned=Coalesce(Subquery(assigned.values('created_at')[:1]), 'first_seen')).values(*FIELDS)


def listing(request, environment, params, queue, cleanup):
    form = ReviewFilterForm(params)
    rows = findings(request, environment, params, queue)
    tasks = [task for task in cleanup if cleanup_in_queue(task, request.user, queue)]
    if not form.is_valid():
        rows, tasks = rows.none(), []
    else:
        values = form.cleaned_data
        if values['task_type'] == 'cleanup': rows = rows.none()
        if values['task_type'] == 'finding': tasks = []
        if values['kind']: tasks = [t for t in tasks if t['item'].finding and t['item'].finding.kind == values['kind']]
        if values['q']:
            term = values['q'].casefold()
            tasks = [t for t in tasks if any(term in value.casefold() for value in
                (t['item'].name, t['item'].path, t['item'].batch.environment.name))]
        if queue == 'all':
            owner, stage = values['owner'], values['review']
            if owner == 'none': tasks = []
            elif owner:
                owner_id = request.user.pk if owner == 'me' else int(owner)
                tasks = [t for t in tasks if t['item'].batch.requested_by_id == owner_id]
            if stage:
                tasks = [t for t in tasks if (stage == 'ready' and t['item'].status == 'verified')
                    or (stage == 'second_review' and t['item'].status == 'cleanup_review' and t['approvals'])
                    or (stage == 'owner_review' and t['item'].status == 'cleanup_review' and not t['approvals'])]
    cleanup_rows = DeletionItem.objects.filter(pk__in=[t['item'].pk for t in tasks])
    order = {'oldest': 'task_assigned', 'updated': 'task_updated', 'name': 'task_name',
             'environment': 'task_environment', 'status': 'task_stage', 'kind': 'task_kind'}.get(params.get('sort'), 'task_assigned')
    direction = params.get('direction') or ('desc' if params.get('sort') == 'updated' else 'asc')
    query = projection(rows).union(projection(cleanup_rows, cleanup=True)).order_by(
        ('-' if direction == 'desc' else '') + order, 'task_kind', 'task_id')
    return query, form, {t['item'].pk: t for t in tasks}


def page_rows(request, page, cleanup, return_to):
    from .findings import LABELS
    from .deletion import permitted, KINDS
    skeleton = list(page.object_list)
    records = {f.pk: f for f in Finding.objects.filter(pk__in=[r['task_id'] for r in skeleton if r['task_kind'] == 'finding']).select_related(
        'environment', 'owner__keycloakidentity', 'owner__ldapidentity').defer('evidence')}
    result = []
    for data in skeleton:
        if data['task_kind'] == 'cleanup':
            task = cleanup[data['task_id']]; item = task['item']
            row = SimpleNamespace(pk=item.pk, environment=item.batch.environment, environment_id=item.batch.environment_id,
                name=item.name, owner=item.batch.requested_by, task=task, review_kind='cleanup',
                task_label='Firewall reference cleanup', stage=task['label'], next_action=task['action_label'],
                review_url=task['url'] + '?' + urlencode({'return_to': return_to}), task_updated=data['task_updated'])
        else:
            row = records.get(data['task_id'])
            if row is None: continue
            row.review_kind = 'finding'; row.task_label = 'Prolonged empty VDI group' if row.qualification_reason.startswith('Prolonged empty VDI group') else LABELS.get(row.kind, row.kind)
            row.can_assign = request.user.is_staff and not row.owner_id and row.workflow_state != 'decommissioned'
            row.can_release = request.user.is_staff and row.owner_id and row.workflow_state != 'decommissioned' and (row.owner_id == request.user.pk or request.user.is_superuser)
            row.can_delete = permitted(request.user) and row.owner_id and row.workflow_state == 'ready' and row.kind in KINDS
            row.assigned_at = data['task_assigned']; row.task_updated = data['task_updated']
            row.approval_count = len([key for key in ('owner', 'second') if key in row.approvals])
            row.readiness = {'status': 'blocked', 'message': 'The finding must be currently observed and eligible for review.'} if not row.present or row.qualification != 'eligible' else {'status':'unverified','message':'Open the review to check current evidence and available actions.'}
            if row.readiness['status']=='blocked': row.can_delete=False
            row.stage = row.get_workflow_state_display()
            row.assignment_notice = row.review_state_label if not row.owner_id and row.workflow_state in ('second_review','ready') else ''
            second = row.workflow_state == 'second_review' and row.owner_id and row.owner_id != request.user.pk and str(row.approvals.get('owner', {}).get('actor_id')) != str(request.user.pk)
            row.next_action = ('Assign owner' if row.can_assign else 'Review changed evidence' if request.user.is_staff and row.readiness['status']=='blocked' else 'Preview cleanup' if row.can_delete else
                'Provide second approval' if request.user.is_staff and second else
                'Review evidence' if request.user.is_staff and row.owner_id == request.user.pk and row.workflow_state == 'owner_review' else 'View review')
            row.review_url = reverse('finding-detail', args=[row.environment_id, row.pk]) + '?' + urlencode({'return_to': return_to})
        result.append(row)
    page.object_list = result
    return page


def workspace(request, environment=None, *, legacy=False):
    from .cleanup_tasks import tasks as cleanup_tasks
    from .history_views import filtered_findings
    from .models import FindingRecalculation
    queue = queue_for(request, legacy=legacy)
    if not request.user.is_staff and queue in ('assigned', 'second'): queue = 'all'
    selected = str(environment.pk) if environment else request.GET.get('environment', '')
    invalid_scope = bool(selected and (not selected.isdecimal() or len(selected)>18))
    if not environment and selected and not invalid_scope:
        environment = Environment.objects.filter(pk=selected).first()
        invalid_scope = environment is None
    params = request.GET.copy()
    params['queue'] = queue
    if queue != 'all':
        for key in ('owner', 'review'): params.pop(key, None)
    params.setdefault('sort', 'name' if legacy else 'oldest')
    params.setdefault('direction', 'desc' if params['sort']=='updated' else 'asc')
    cleanup = [] if invalid_scope else cleanup_tasks(request.user, environment=selected or None, queue='all')
    query, form, selected_cleanup = listing(request, environment, params, queue, cleanup)
    if not request.user.is_staff:
        for key in ('data-user-search','data-reviewer-combobox','data-reviewer-filter'):
            form.fields['owner'].widget.attrs.pop(key,None)
    if invalid_scope: query = query.none()
    paginator = Paginator(query, table_page_size(request))
    page = paginator.get_page(params.get('page'))
    page = page_rows(request, page, selected_cleanup, request.get_full_path())
    base, _, _ = filtered_findings(request, environment, {}, include_history=True)
    if invalid_scope: base = base.none()
    counts = {key: in_queue(base, request.user, key).count() + sum(cleanup_in_queue(t, request.user, key) for t in cleanup) for key, _ in QUEUES}
    queue_links = links(request, queue, environment, counts)
    qualifiers = list((environment.findings if environment else Finding.objects).filter(present=True).values('qualification').annotate(total=Count('pk')).order_by('qualification')) if not invalid_scope else []
    labels = dict(Finding._meta.get_field('qualification').choices)
    for entry in qualifiers:
        entry['label'] = labels.get(entry['qualification'], entry['qualification'].replace('_', ' ').capitalize())
    context = {'page': page, 'environment': environment, 'selected_environment': environment,
        'work_environment': selected, 'queue': queue, 'queue_links': queue_links, 'queue_counts': counts,
        'queue_label': dict(QUEUES)[queue], 'scope_label': environment.name if environment else 'All environments',
        'filter_form': form, 'query': params.get('q',''), 'sort': params['sort'],
        'review_return': request.get_full_path(), 'review_workspace': True, 'filters_active': any(params.get(k) for k in ('q','kind','owner','review','task_type')),
        'show_release_controls': request.user.is_staff, 'show_deletion_controls': request.user.is_superuser and queue == 'ready',
        'cleanup_tasks': cleanup, 'cleanup_limited': len(cleanup)>=500,
        'qualification_counts': qualifiers, 'table_total': counts[queue],
        'historical_finding_count': (environment.findings if environment else Finding.objects).filter(present=False).count() if not invalid_scope else 0,
        'recalculation': FindingRecalculation.objects.filter(environment=environment).first() if environment else None,
        'has_findings': any(r.review_kind=='finding' for r in page), 'has_cleanup': any(r.review_kind=='cleanup' for r in page),
        'environments': Environment.objects.only('pk','name'), 'query_string': params.urlencode()}
    return render(request, 'inventory/reviews.html', context)
