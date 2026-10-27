"""Read-only adapters for existing review audit records and deletion requests."""
from uuid import UUID
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Q, Prefetch, Case, When, Value, BooleanField
from django.urls import reverse
from django.utils.dateparse import parse_date
from .models import AuditEvent, DeletionBatch, DeletionItem, Environment, Finding
from .deletion import permitted
from .user_labels import user_label

ACTIVE = ('preview_queued', 'checking', 'queued', 'running', 'cancelling')


def filters(request):
    result = {key: request.GET.get(key, '').strip()[:255] for key in ('q', 'actor', 'outcome', 'start', 'end')}
    result['errors'] = []
    for key in ('start', 'end'):
        try:
            result[key + '_date'] = parse_date(result[key]) if result[key] else None
        except ValueError:
            result[key + '_date'] = None
        if result[key] and not result[key + '_date']:
            result['errors'].append('Enter a valid date in YYYY-MM-DD format.')
    if result['start_date'] and result['end_date'] and result['start_date'] > result['end_date']:
        result['errors'].append('Start date must be before or equal to end date.')
    users = get_user_model().objects.all()
    if result['actor']:
        term = result['actor']
        users = users.filter(Q(username__icontains=term) | Q(first_name__icontains=term) | Q(last_name__icontains=term))
    result['actor_ids'] = users.values_list('pk', flat=True)
    return result


def dated(rows, options, field='created_at'):
    if options['errors']:
        return rows.none()
    for key, lookup in [('start', 'gte'), ('end', 'lte')]:
        if options[key + '_date']:
            rows = rows.filter(**{field + '__date__' + lookup: options[key + '_date']})
    return rows


def audit_rows(request, options, selected, kind):
    allowed = Q(action__startswith='finding.')
    if permitted(request.user):
        allowed |= Q(action__startswith='nsx.deletion_') | Q(action='nsx.access_denied') | Q(action__in=['comparison.cancel_requested', 'recalculation.cancel_requested'])
    rows = AuditEvent.objects.filter(allowed)
    if not permitted(request.user):
        rows = rows.exclude(action__startswith='finding.nsx_')
    if kind == 'approval': rows = rows.filter(Q(action__startswith='finding.') | Q(action__in=['nsx.deletion_cleanup_approved', 'nsx.deletion_cleanup_rejected']))
    elif kind == 'deletion': rows = rows.filter(Q(action__startswith='nsx.deletion_') | Q(action='nsx.access_denied', target_type='DeletionItem'))
    elif kind in ('comparison', 'review'):
        rows = rows.filter(action=('comparison' if kind == 'comparison' else 'recalculation') + '.cancel_requested')
    elif kind: rows = rows.none()
    if selected:
        rows = rows.filter(details__environment_id=int(selected)) if selected.isdecimal() and len(selected)<19 else rows.none()
    if options['actor']:
        rows = rows.filter(actor_id_text__in=[str(pk) for pk in options['actor_ids']])
    if options['q']:
        term = options['q']
        rows = rows.filter(Q(details__name__icontains=term) | Q(details__path__icontains=term) | Q(details__ticket__icontains=term) | Q(details__change_ticket__icontains=term))
    if options['outcome']:
        term = options['outcome']
        rows = rows.filter(Q(outcome=term) | Q(details__to=term) | Q(action='nsx.deletion_' + term))
    if request.GET.get('status') == 'failed':
        rows = rows.filter(Q(outcome='failed') | Q(action='nsx.deletion_failed') | Q(action='nsx.deletion_uncertain'))
    return dated(rows, options).order_by('-created_at', '-pk')


def audit_cards(rows):
    rows = list(rows)
    actors = {str(user.pk): user_label(user) for user in get_user_model().objects.filter(pk__in=[int(e.actor_id_text) for e in rows if e.actor_id_text.isdecimal() and len(e.actor_id_text)<19]).select_related('keycloakidentity', 'ldapidentity')}
    envs = dict(Environment.objects.values_list('pk', 'name'))
    finding_ids = [int(e.target_id) for e in rows if e.target_type == 'Finding' and e.target_id.isdecimal()]
    findings = dict(Finding.objects.filter(pk__in=finding_ids).values_list('pk','environment_id'))
    candidates=[]
    for entry in rows:
        value=entry.details.get('batch') or (entry.target_id if entry.target_type=='DeletionBatch' else '')
        try: candidates.append(UUID(str(value)))
        except ValueError: pass
    batch_ids = {str(pk) for pk in DeletionBatch.objects.filter(pk__in=candidates).values_list('pk', flat=True)}
    cards = []
    for entry in rows:
        data = entry.details
        is_deletion = entry.action.startswith('nsx.')
        action = entry.action.split('.', 1)[-1]
        outcome = data.get('status') or (action.removeprefix('deletion_') if is_deletion else data.get('to', entry.outcome))
        url = ''
        finding_url = ''
        link_label = 'View deletion request' if is_deletion else 'View finding'
        if entry.target_type == 'Finding' and entry.target_id.isdecimal() and int(entry.target_id) in findings:
            url = reverse('finding-detail', args=[findings[int(entry.target_id)], entry.target_id])
            finding_url = url
        batch = data.get('batch') or (entry.target_id if entry.target_type == 'DeletionBatch' else '')
        if entry.target_type == 'AuditJob' and data.get('collection_job'):
            url = reverse('collection-diagnostics', args=[data['collection_job']])
            link_label = 'View collection diagnostics'
        if str(batch) in batch_ids:
            url = reverse('deletion-detail', args=[batch])
            link_label = 'View deletion request'
        if entry.target_type == 'DeletionItem' and action.startswith('deletion_cleanup_') and entry.target_id.isdecimal():
            url = reverse('deletion-reference-cleanup', args=[entry.target_id])
            link_label = 'Review cleanup plan'
        label = (entry.action.split('.')[0] + ' cancellation requested').capitalize() if action == 'cancel_requested' else action.replace('_',' ').capitalize()
        label = {'deletion_cleanup_approved': 'Cleanup approval recorded', 'deletion_cleanup_rejected': 'Cleanup rejected',
                 'deletion_cleanup_recheck': 'Cleanup revalidating'}.get(action, label)
        if action == 'approve':
            label = 'Second approval' if data.get('from') == 'second_review' else 'First approval' if data.get('to') == 'second_review' else 'Approval (single approver)'
        cards.append(dict(kind=label, category='deletion' if is_deletion else 'approval',
            environment=envs.get(data.get('environment_id'), 'Retained environment'), status=str(outcome).replace('_', ' '),
            actor=data.get('actor_label') or actors.get(entry.actor_id_text, 'Deleted account #' + entry.actor_id_text if entry.actor_id_text else 'System / background worker'),
            at=entry.created_at, detail=data.get('reason') or data.get('message') or '',
            name=data.get('name') or data.get('path') or '', path=data.get('path',''),
            ticket=data.get('ticket') or data.get('change_ticket',''), url=url, finding_url=finding_url, link_label=link_label, unavailable='Deletion request no longer available' if is_deletion else 'Finding no longer available'))
    return cards


def deletion_cards(request, options, selected, active_only=False):
    if not permitted(request.user): return []
    rows = DeletionBatch.objects.select_related('environment', 'requested_by__keycloakidentity', 'requested_by__ldapidentity')
    if selected:
        rows = rows.filter(environment_id=selected) if selected.isdecimal() and len(selected)<19 else rows.none()
    if active_only: rows = rows.filter(status__in=ACTIVE)
    if request.GET.get('status') == 'failed':
        rows = rows.filter(pk__in=DeletionItem.objects.filter(status__in=['failed','uncertain']).values('batch_id'))
    if options['actor']: rows = rows.filter(Q(requested_by_id__in=options['actor_ids']) | Q(confirmed_by_id__in=options['actor_ids']))
    matches = DeletionItem.objects.all()
    if options['q']:
        term = options['q']
        matches = matches.filter(Q(name__icontains=term) | Q(path__icontains=term) | Q(batch__ticket__icontains=term))
    if options['outcome']: matches = matches.filter(status=options['outcome'])
    if options['q'] or options['outcome']: rows = rows.filter(pk__in=matches.values('batch_id'))
    rows = dated(rows, options).prefetch_related(Prefetch('items', queryset=DeletionItem.objects.annotate(has_configuration=Case(When(before={}, then=Value(False)), default=Value(True), output_field=BooleanField()), has_cleanup=Case(When(cleanup_plan=[], then=Value(False)), default=Value(True), output_field=BooleanField())).only('pk','batch_id','finding_id','name','path','status','message'))).order_by('-created_at')[:60]
    cards=[]
    for batch in rows:
        items=list(batch.items.all())
        deleted=sum(item.status=='deleted' for item in items)
        cards.append(dict(kind='NSX deletion' if batch.confirmed_at else 'Deletion preview', environment=batch.environment.name,
            status=batch.status_label, active=batch.status in ACTIVE, at=batch.created_at, started=batch.confirmed_at,
            finished=batch.finished_at, actor=user_label(batch.requested_by) if batch.requested_by else 'Deleted account',
            ticket=batch.ticket, environment_id=batch.environment_id, detail=f'{len(items)} objects · {deleted} deleted' + (f' · Execution confirmed by {user_label(batch.confirmed_by)}' if batch.confirmed_by else ''), object_results=items,
            url=reverse('deletion-detail',args=[batch.pk]), link_label='View deletion request'))
    return cards
