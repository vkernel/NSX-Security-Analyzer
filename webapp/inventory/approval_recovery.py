"""Conservative, explicit recovery of a gap-invalidated first approval."""
from copy import deepcopy
from django.core.exceptions import ValidationError
from .models import Snapshot, SnapshotFindingAssessment, SnapshotFindingEvidence, FindingRecalculation
from .finding_workflow import approval_readiness, review_fingerprint, coverage_key, event, FIELDS
from .observation import condition_fingerprint


def preview(finding):
    def blocked(reason):
        return {'allowed': False, 'reason': reason}
    if finding.workflow_state != 'owner_review' or finding.approvals or finding.required_approvals != 2:
        return blocked('Recovery requires an owner review with no active approvals and two-person approval enabled.')
    invalidation = finding.events.filter(details__action='approval_invalidated').first()
    if not invalidation:
        return blocked('No archived approval is available.')
    details = invalidation.details
    if details.get('reason') != 'Current evidence no longer qualifies under the finding criteria.':
        return blocked('The recorded invalidation was not solely an eligibility interruption.')
    decision = details.get('prior_approvals', {}).get('owner', {})
    if (not decision or decision.get('required_approvals') != 2 or not decision.get('at')
            or str(finding.owner_id) != str(decision.get('actor_id'))
            or not finding.owner or not finding.owner.is_active or not finding.owner.is_staff):
        return blocked('The original approver must still be the active, eligible owner.')
    if finding.events.filter(pk__gt=invalidation.pk, details__action__isnull=False).exists():
        return blocked('A subsequent workflow action prevents automatic recovery.')
    replay = FindingRecalculation.objects.filter(environment=finding.environment, status='completed', finished_at__gt=invalidation.created_at).first()
    if not replay or finding.kind not in replay.kinds:
        return blocked('Complete a criteria recalculation for this finding type after the invalidation.')
    ready = approval_readiness(finding)
    if ready['status'] != 'ready':
        return blocked(ready['message'])
    if decision.get('fingerprint') != review_fingerprint(finding.kind, finding.evidence):
        return blocked('Current review evidence differs from the original approval.')
    if decision.get('coverage_key', '') != ready['coverage_key']:
        return blocked('Reference-search coverage differs from the original approval.')
    if not any(decision.get('evidence', {}).get(key) for key in ('unique_id', 'created_at', 'rule_id', 'policy_rule_id')):
        return blocked('The archived approval lacks stable object identity evidence.')
    original = Snapshot.objects.filter(pk=decision.get('snapshot_id'), environment=finding.environment).first()
    if not original or not finding.observation_started or finding.observation_started > original.generated_at:
        return blocked('Retained observation continuity does not extend back to the approved snapshot.')
    # The old event records a generic eligibility reason. Require the immutable
    # assessment from the interruption itself to prove a collection gap.
    interruption = Snapshot.objects.filter(environment=finding.environment, testing=False, imported=False,
        generated_at__gt=original.generated_at, generated_at__lte=invalidation.created_at).order_by('-generated_at').first()
    gap_proof = SnapshotFindingAssessment.objects.filter(snapshot=interruption, path=finding.path, kind=finding.kind,
        assessment__status='observing', assessment__reason__startswith='Observation restarted after a gap exceeding ')
    if not gap_proof.exists():
        return blocked('Retained assessments do not prove a gap-only interruption. Fresh owner approval is required.')
    expected = condition_fingerprint(finding.kind, decision.get('evidence', {}))
    snapshots = Snapshot.objects.filter(environment=finding.environment, testing=False, imported=False,
        generated_at__gte=original.generated_at, generated_at__lte=finding.snapshot.generated_at)
    evidence_by_snapshot = dict(SnapshotFindingEvidence.objects.filter(snapshot__in=snapshots,
        kind=finding.kind, path=finding.path).values_list('snapshot_id', 'fingerprint'))
    fields = ['pk', 'needs_review', 'summary']
    if finding.kind == 'unused':
        fields.append('report__search_coverage')
    for snapshot in snapshots.values(*fields).iterator():
        if snapshot['needs_review'] or snapshot['summary'].get('inventory_warnings'):
            return blocked('The retained interval contains incomplete coverage; recovery cannot be proven safe.')
        if finding.kind == 'unused' and coverage_key(snapshot['report__search_coverage']) != decision.get('coverage_key', ''):
            return blocked('Reference-search coverage changed within the retained interval.')
        if evidence_by_snapshot.get(snapshot['pk']) != expected:
            return blocked('Missing or changed retained evidence prevents recovery.')
    return {'allowed': True, 'reason': 'Retained evidence supports recovering the first approval only.',
            'decision': decision, 'event_id': invalidation.pk}


def recover(finding, actor, revision, event_id):
    # Caller holds environment and finding locks, matching collection/review writes.
    if not actor.is_active or not actor.is_superuser:
        raise ValidationError('Administrator access is required.')
    if str(finding.revision) != str(revision):
        raise ValidationError('This finding changed. Open a new recovery preview.')
    result = preview(finding)
    if not result['allowed']:
        raise ValidationError(result['reason'])
    if str(result['event_id']) != str(event_id):
        raise ValidationError('Approval history changed. Open a new recovery preview.')
    finding.approvals = {'owner': deepcopy(result['decision'])}
    finding.workflow_state = 'second_review'
    finding.revision += 1
    finding.save(update_fields=FIELDS + ['revision'])
    event(finding, 'approval_recovered', 'owner_review',
          'Administrator recovered the original first approval after verifying gap continuity; independent second approval is required.',
          actor=actor, extra={'invalidation_event_id': result['event_id'], 'decision': result['decision']})
