"""Two-person decisions; callers serialize against collection using the environment lock."""
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import FindingEvent, SnapshotCoverage
import hashlib
import json
from .audit_events import record

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
    label = actor.get_username() if actor else 'System'
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


def evidence_error(finding):
    from .usability import policy
    if not finding.present or finding.qualification != 'eligible':
        return 'The finding must be currently observed and eligible for review.'
    latest = finding.environment.snapshots.filter(testing=False, imported=False).only('pk', 'generated_at', 'needs_review').first()
    if not latest or latest.pk != finding.snapshot_id:
        return 'Review evidence has not been updated to the latest full collection.'
    if latest.generated_at < timezone.now() - timedelta(hours=policy().stale_hours):
        return 'Collection evidence is stale. Run a new full collection before approval.'
    coverage = SnapshotCoverage.objects.filter(snapshot=latest).first()
    if latest.needs_review or coverage is None or coverage.issue_count:
        return 'The latest collection must have a prepared, complete coverage check before approval.'
    return ''


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
            decision = {'actor_id': str(actor.pk), 'actor_name': actor.get_username(),
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
