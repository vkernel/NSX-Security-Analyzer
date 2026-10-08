"""Two-person decisions; callers serialize against collection using the environment lock."""
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
FIELDS = ['workflow_state', 'approvals', 'change_ticket']


def review_fingerprint(kind, evidence):
    # Counter acquisition method can change without changing configuration or condition.
    stable = {key: value for key, value in evidence.items() if key != 'statistics_source'}
    return hashlib.sha256(json.dumps([kind, stable], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def event(finding, action, before, reason, actor=None, extra=None):
    details = {'action': action, 'from': before, 'to': finding.workflow_state,
               'reason': reason, 'environment_id': finding.environment_id, 'kind': finding.kind,
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
        if not isinstance(search_coverage, dict) or search_coverage.get('mode') != 'all_types':
            return 'Unused-object approval requires unrestricted reference-search coverage. Compatibility or unrecorded search may miss references.'
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


def evidence_error(finding):
    from .usability import policy
    if not finding.present or finding.qualification != 'eligible':
        return 'The finding must be currently observed and eligible for review.'
    latest = finding.environment.snapshots.filter(testing=False, imported=False).only('pk', 'generated_at').first()
    if not latest or latest.pk != finding.snapshot_id:
        return 'Review evidence has not been updated to the latest full collection.'
    if latest.generated_at < timezone.now() - timedelta(hours=policy().stale_hours):
        return 'Collection evidence is stale. Run a new full collection before approval.'
    # Read one compact object, never the full report or rendered evidence.
    view = 'inventory' if finding.kind in ('empty_group', 'unused') else 'dfw'
    row = SnapshotRecord.objects.filter(snapshot=latest, view=view, compact__path=finding.path).values_list('compact', flat=True).first()
    search = Snapshot.objects.filter(pk=latest.pk).values_list('report__search_coverage', flat=True).get() if finding.kind == 'unused' else None
    return relevant_evidence_error(finding.kind, row, search, latest.generated_at, finding.environment.sync_interval_minutes)


@transaction.atomic
def decide(finding, actor, action, reason, owner=None, ticket=''):
    if not actor.is_active or not actor.is_staff:
        raise ValidationError('Operator or administrator access is required.')
    reason, ticket = reason.strip(), ticket.strip()
    if not reason:
        raise ValidationError('A reason is required for every workflow action.')
    before = finding.workflow_state
    extra = {}
    if action == 'assign':
        if not owner or not owner.is_active or not owner.is_staff:
            raise ValidationError('Choose an active operator or administrator as owner.')
        if before == 'decommissioned':
            raise ValidationError('A completed finding cannot be reassigned.')
        if finding.owner_id == owner.pk:
            raise ValidationError('This user is already the owner. Use Reopen review to restart a rejected review.')
        extra = {'previous_owner_id': finding.owner_id, 'prior_approvals': finding.approvals}
        finding.owner = owner
        finding.workflow_state, finding.approvals = 'owner_review', {}
    elif action == 'reopen':
        if before != 'rejected':
            raise ValidationError('Only rejected reviews can be reopened manually.')
        finding.workflow_state = 'owner_review' if finding.owner_id else 'unassigned'
        finding.approvals = {}
    elif action in ('approve', 'reject'):
        if before == 'owner_review':
            if actor.pk != finding.owner_id:
                raise ValidationError('Only the assigned owner may make the first decision.')
        elif before == 'second_review':
            if actor.pk == finding.owner_id or str(actor.pk) == str(finding.approvals.get('owner', {}).get('actor_id')):
                raise ValidationError('The second reviewer must be a different user from the owner and first approver.')
            if not finding.approvals.get('owner'):
                raise ValidationError('The owner approval is missing. Reassign this finding for review.')
        else:
            raise ValidationError('This finding is not awaiting an approval decision.')
        if action == 'reject':
            finding.workflow_state = 'rejected'
        else:
            error = evidence_error(finding)
            if error: raise ValidationError(error)
            fingerprint = review_fingerprint(finding.kind, finding.evidence)
            if before == 'second_review' and finding.approvals['owner']['fingerprint'] != fingerprint:
                raise ValidationError('Relevant evidence changed. A new owner review is required.')
            decision = {'actor_id': str(actor.pk), 'actor_name': user_label(actor),
                        'at': timezone.now().isoformat(), 'reason': reason,
                        'snapshot_id': str(finding.snapshot_id), 'fingerprint': fingerprint,
                        'evidence': finding.evidence}
            finding.approvals = {**finding.approvals, 'owner' if before == 'owner_review' else 'second': decision}
            extra = {'decision': decision}
            finding.workflow_state = 'second_review' if before == 'owner_review' else 'ready'
    elif action == 'complete':
        if before != 'ready' or not finding.approvals.get('second'):
            raise ValidationError('Two approvals are required before recording decommissioning.')
        if not ticket:
            raise ValidationError('A change-ticket reference is required to record completion.')
        error = evidence_error(finding)
        if error: raise ValidationError(error)
        if finding.approvals['second']['fingerprint'] != review_fingerprint(finding.kind, finding.evidence):
            raise ValidationError('Relevant evidence changed. A new owner review is required.')
        finding.change_ticket, finding.workflow_state = ticket, 'decommissioned'
        extra = {'change_ticket': ticket}
    else:
        raise ValidationError('Unknown workflow action.')
    finding.revision += 1
    finding.save(update_fields=FIELDS + ['owner', 'revision'])
    event(finding, action, before, reason, actor, extra)
