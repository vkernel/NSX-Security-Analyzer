"""Configurable approval decisions; callers serialize against collection using the environment lock."""
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import FindingEvent, SnapshotRecord, Snapshot
import hashlib
import json
from .audit_events import record
from .user_labels import user_label

STATES = [('unassigned', 'Unassigned'), ('owner_review', 'Owner review'),
          ('second_review', 'Awaiting second approval'), ('ready', 'Ready for decommissioning'),
          ('decommissioned', 'Decommissioned'), ('rejected', 'Rejected')]
FIELDS = ['workflow_state', 'approvals', 'change_ticket', 'required_approvals']


def review_fingerprint(kind, evidence):
    # Counter acquisition method can change without changing configuration or condition.
    stable = {key: value for key, value in evidence.items() if key != 'statistics_source'}
    return hashlib.sha256(json.dumps([kind, stable], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def event(finding, action, before, reason, actor=None, extra=None):
    details = {'action': action, 'from': before, 'to': finding.workflow_state,
               'required_approvals': finding.required_approvals, 'reason': reason, 'environment_id': finding.environment_id, 'kind': finding.kind,
               'path': finding.path, 'name': finding.name, 'owner_id': finding.owner_id,
               'snapshot_id': str(finding.snapshot_id or ''), **(extra or {})}
    label = user_label(actor)[:255] if actor else 'System'
    FindingEvent.objects.create(finding=finding, actor=actor, actor_label=label,
        message=f'{dict(STATES).get(before, before)} → {dict(STATES)[finding.workflow_state]}: {reason}', details=details)
    record('finding.' + action, 'Finding', finding.pk, actor=actor.pk if actor else '', details=details)


def invalidate(finding, reason):
    if finding.workflow_state not in ('second_review', 'ready'):
        return
    before = finding.workflow_state
    prior = finding.approvals
    finding.workflow_state = 'owner_review' if finding.owner_id else 'unassigned'
    finding.approvals = {}
    event(finding, 'approval_invalidated', before, reason, extra={'prior_approvals': prior})


def compatibility_search(search):
    return (isinstance(search, dict) and search.get('mode') == 'explicit_types'
            and isinstance(search.get('resource_types'), list) and bool(search['resource_types'])
            and all(isinstance(item, str) and item.strip() for item in search['resource_types'])
            and not search.get('errors') and not search.get('failed'))


def coverage_key(search):
    search = search if isinstance(search, dict) else {}
    types = search.get('resource_types')
    types = sorted({item for item in types if isinstance(item, str)}) if isinstance(types, list) else []
    return hashlib.sha256(json.dumps([search.get('mode'), types]).encode()).hexdigest()


def coverage_changed(finding, search):
    if finding.kind != 'unused' or not finding.approvals.get('owner'):
        return False
    saved = finding.approvals['owner'].get('coverage_key')
    return saved != coverage_key(search) if saved else compatibility_search(search)


def relevant_evidence_error(kind, row, search_coverage, stamp, interval_minutes):
    """The same finding-specific checks serve approval and collection revalidation."""
    if not row:
        return 'Evidence for this object is not prepared. Collect a new snapshot or prepare its saved index before approval.'
    if row.get('audit_exclusions'):
        return 'This object is excluded from finding checks and cannot be approved.'
    if kind == 'empty_group':
        return '' if row.get('membership') == 'empty' else 'Group membership was not confirmed empty in this collection.'
    if kind == 'unused':
        if row.get('usage') != 'unused_candidate':
            return 'Reference checks did not confirm this object as an unused candidate.'
        if not isinstance(search_coverage, dict) or search_coverage.get('errors') or search_coverage.get('failed') or (search_coverage.get('mode') != 'all_types' and not compatibility_search(search_coverage)):
            return 'Reference search is failed or unrecorded. Run a successful collection before approving an unused object.'
        return ''
    if kind == 'zero_hits':
        from django.utils.dateparse import parse_datetime
        try:
            checked = parse_datetime(row.get('statistics_checked_at') or '')
            fresh = checked is not None and checked.tzinfo is not None and checked <= stamp and stamp-checked <= timedelta(minutes=max(1440, 2*interval_minutes))
        except (TypeError, ValueError):
            fresh = False
        if row.get('hit_status') != 'zero_hits' or type(row.get('hit_count')) is not int or row['hit_count'] != 0 or not fresh or row.get('disabled') is True:
            return 'Zero-hit approval requires fresh, successful zero-hit counters for this enabled rule.'
        return ''
    if kind == 'disabled':
        return '' if row.get('disabled') is True else 'The rule was not confirmed disabled in this collection.'
    if kind == 'empty_policy':
        return '' if row.get('status') == 'empty' and type(row.get('rule_count')) is int and row['rule_count'] == 0 else 'The policy rule inventory was not successfully confirmed empty.'
    return 'This finding type does not have sufficient supported evidence for decommissioning approval.'


def approval_readiness(finding):
    def blocked(message): return {'status': 'blocked', 'message': message}
    from .usability import policy
    if not finding.present or finding.qualification != 'eligible':
        return blocked('The finding must be currently observed and eligible for review.')
    latest = finding.environment.snapshots.filter(testing=False, imported=False).only('pk', 'generated_at').first()
    if not latest or latest.pk != finding.snapshot_id:
        return blocked('Review evidence has not been updated to the latest full collection.')
    if latest.generated_at < timezone.now() - timedelta(hours=policy().stale_hours):
        return blocked('Collection evidence is stale. Run a new full collection before approval.')
    # Read one compact object, never the full report or rendered evidence.
    view = 'inventory' if finding.kind in ('empty_group', 'unused') else 'dfw'
    row = SnapshotRecord.objects.filter(snapshot=latest, view=view, compact__path=finding.path).values_list('compact', flat=True).first()
    search = Snapshot.objects.filter(pk=latest.pk).values_list('report__search_coverage', flat=True).get() if finding.kind == 'unused' else None
    error = relevant_evidence_error(finding.kind, row, search, latest.generated_at, finding.environment.sync_interval_minutes)
    if error: return blocked(error)
    manual = finding.kind == 'unused' and compatibility_search(search)
    return {'status': 'manual' if manual else 'ready',
            'message': 'Reference-search coverage is limited. Each reviewer must independently check dependencies outside the collected search results.' if manual else 'Relevant saved evidence is current and supports review.',
            'coverage': search if finding.kind == 'unused' else {},
            'coverage_key': coverage_key(search) if finding.kind == 'unused' else '', 'row': row}


def evidence_error(finding):
    readiness = approval_readiness(finding)
    return readiness['message'] if readiness['status'] == 'blocked' else ''



@transaction.atomic
def decide(finding, actor, action, reason, owner=None, ticket='', manual_verified=False, manual_checks='', evidence_reference=''):
    if not actor.is_active or not actor.is_staff:
        raise ValidationError('Operator or administrator access is required.')
    reason, ticket = reason.strip(), ticket.strip()
    if not reason:
        raise ValidationError('A reason is required for every workflow action.')
    before = finding.workflow_state
    if finding.required_approvals not in (1, 2):
        raise ValidationError('Invalid approval requirement.')
    extra = {}
    if action == 'assign':
        if not owner or not owner.is_active or not owner.is_staff:
            raise ValidationError('Choose an active operator or administrator as owner.')
        if before == 'decommissioned':
            raise ValidationError('A completed finding cannot be reassigned.')
        if finding.owner_id == owner.pk:
            raise ValidationError('This user is already the owner. Use Reopen review to restart a rejected review.')
        extra = {'previous_owner_id': finding.owner_id, 'prior_approvals': finding.approvals}
        from .usability import policy
        finding.required_approvals = policy().required_approvals
        finding.owner = owner
        finding.workflow_state, finding.approvals = 'owner_review', {}
    elif action == 'reopen':
        if before != 'rejected':
            raise ValidationError('Only rejected reviews can be reopened manually.')
        from .usability import policy
        finding.required_approvals = policy().required_approvals
        finding.workflow_state = 'owner_review' if finding.owner_id else 'unassigned'
        finding.approvals = {}
    elif action in ('approve', 'reject'):
        if before == 'owner_review':
            if actor.pk != finding.owner_id:
                raise ValidationError('Only the assigned owner may make the first decision.')
        elif before == 'second_review':
            if finding.required_approvals != 2:
                raise ValidationError('This review does not require a second approval.')
            if actor.pk == finding.owner_id or str(actor.pk) == str(finding.approvals.get('owner', {}).get('actor_id')):
                raise ValidationError('The second reviewer must be a different user from the owner and first approver.')
            if not finding.approvals.get('owner'):
                raise ValidationError('The owner approval is missing. Reassign this finding for review.')
        else:
            raise ValidationError('This finding is not awaiting an approval decision.')
        if action == 'reject':
            finding.workflow_state = 'rejected'
        else:
            readiness = approval_readiness(finding)
            if readiness['status'] == 'blocked': raise ValidationError(readiness['message'])
            manual = readiness['status'] == 'manual'
            if before == 'second_review' and coverage_changed(finding, readiness['coverage']):
                raise ValidationError('Reference-search coverage changed. A new owner review is required.')
            if manual and (manual_verified is not True or not manual_checks.strip() or not evidence_reference.strip()):
                raise ValidationError('Confirm your independent dependency check, describe what you checked, and provide a ticket or evidence reference.')
            if manual and before == 'second_review' and not finding.approvals['owner'].get('manual_verified'):
                raise ValidationError('The owner must first complete the manual verification route.')
            fingerprint = review_fingerprint(finding.kind, finding.evidence)
            if before == 'second_review' and finding.approvals['owner']['fingerprint'] != fingerprint:
                raise ValidationError('Relevant evidence changed. A new owner review is required.')
            decision = {'required_approvals': finding.required_approvals, 'actor_id': str(actor.pk), 'actor_name': user_label(actor),
                        'at': timezone.now().isoformat(), 'reason': reason,
                        'snapshot_id': str(finding.snapshot_id), 'fingerprint': fingerprint,
                        'evidence': finding.evidence, 'coverage': readiness['coverage'], 'coverage_key': readiness['coverage_key'],
                        'manual_verified': manual, 'manual_checks': manual_checks.strip() if manual else '',
                        'evidence_reference': evidence_reference.strip() if manual else ''}
            finding.approvals = {**finding.approvals, 'owner' if before == 'owner_review' else 'second': decision}
            extra = {'decision': decision}
            finding.workflow_state = 'second_review' if before == 'owner_review' and finding.required_approvals == 2 else 'ready'
    elif action == 'complete':
        stages = ('owner', 'second') if finding.required_approvals == 2 else ('owner',)
        if before != 'ready' or not all(finding.approvals.get(stage) for stage in stages):
            raise ValidationError('The required approvals must be recorded before decommissioning.')
        if finding.required_approvals == 2 and finding.approvals['owner'].get('actor_id') == finding.approvals['second'].get('actor_id'):
            raise ValidationError('Two different reviewers are required.')
        if not ticket:
            raise ValidationError('A change-ticket reference is required to record completion.')
        readiness = approval_readiness(finding)
        if readiness['status'] == 'blocked': raise ValidationError(readiness['message'])
        if coverage_changed(finding, readiness['coverage']):
            raise ValidationError('Reference-search coverage changed. A new owner review is required.')
        if readiness['status'] == 'manual' and not all(finding.approvals.get(stage, {}).get('manual_verified') for stage in stages):
            raise ValidationError('Each required reviewer must record independent manual verification before decommissioning.')
        if finding.approvals[stages[-1]]['fingerprint'] != review_fingerprint(finding.kind, finding.evidence):
            raise ValidationError('Relevant evidence changed. A new owner review is required.')
        finding.change_ticket, finding.workflow_state = ticket, 'decommissioned'
        extra = {'change_ticket': ticket}
    else:
        raise ValidationError('Unknown workflow action.')
    finding.revision += 1
    finding.save(update_fields=FIELDS + ['owner', 'revision'])
    event(finding, action, before, reason, actor, extra)
