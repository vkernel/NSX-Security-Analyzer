"""Narrow execution-only replacement for incomplete global VM inventory."""
from datetime import timedelta
import hashlib
import json
from django.utils import timezone
from . import collector, observation
from .evidence_coverage import snapshot_issues, relevant
from .models import Snapshot, SnapshotRecord
from .transient import matching


def historical_group_evidence(finding, latest, *, blockers=None):
    """Return a fresh approved source only when every intervening gap is VM-only.

    Does not qualify a finding, add observations, or authorize a DELETE. The caller
    must still verify all approvals, live identity, membership and references.
    """
    def blocked(code, message):
        if blockers is not None:
            blockers.append(f'[{code}] {message}')
        return None

    config = finding.evidence.get('configuration') or {}
    if finding.kind != 'empty_group' or finding.workflow_state != 'ready':
        return blocked('finding_not_ready', 'Direct verification requires an empty-group finding already ready for decommissioning.')
    if not collector.vm_fast_eligible(config):
        return blocked('unsupported_group_definition', 'The saved group definition is not supported for direct verification. It must be a local group with exactly one VM Name or Tag EQUALS condition, without nested, mixed or extended expressions. Review the group Technical details.')
    source = Snapshot.objects.filter(pk=finding.snapshot_id, environment_id=finding.environment_id,
                                     testing=False, imported=False).only('pk','generated_at','summary').first()
    if not source or not latest:
        return blocked('missing_history', 'The retained approved collection or latest full collection is unavailable.')
    if source.generated_at >= latest.generated_at:
        return blocked('no_prior_verified_source', 'The approved evidence is not from an earlier verified collection.')
    if relevant(snapshot_issues(source), finding.kind, finding.path):
        return blocked('source_coverage_incomplete', 'The retained approved collection itself has incomplete evidence required by this finding.')
    # Never bypass a newer verified snapshot, an unknown warning, or another failed area.
    intermediate = finding.environment.snapshots.filter(testing=False, imported=False,
        generated_at__gt=source.generated_at).only('pk','generated_at','summary')
    for snapshot in intermediate:
        entries = snapshot_issues(snapshot)
        if not entries:
            return blocked('newer_verified_collection', f'Collection {snapshot.pk} ({snapshot.generated_at.isoformat()}) has no inventory warnings. Wait for finding evaluation to apply that collection, then create a new preview.')
        if any(entry.get('endpoint') != '/infra/realized-state/virtual-machines'
                              or entry.get('scope') != 'VM inventory' for entry in entries):
            return blocked('other_coverage_warning', f'Collection {snapshot.pk} ({snapshot.generated_at.isoformat()}) contains warnings beyond the supported global VM inventory warning. Review its collection coverage.')
        saved = SnapshotRecord.objects.filter(snapshot=snapshot, view='inventory', compact__path=finding.path).values('compact', 'data__configuration').first()
        row = saved['compact'] if saved else None
        context = f'Collection {snapshot.pk} ({snapshot.generated_at.isoformat()})'
        if not row:
            return blocked('missing_group_record', context + ' has no indexed record for this group.')
        if row.get('audit_exclusions'):
            return blocked('group_excluded', context + ' excludes this group from evaluation.')
        if row.get('membership') != 'empty':
            return blocked('membership_not_empty', context + ' does not confirm empty membership for this group.')
        if saved['data__configuration'] != config:
            return blocked('configuration_changed', context + ' has a different group configuration from the approved evidence. Collect and review the current definition.')
        if any(row.get(key) != finding.evidence.get(key) for key in ('unique_id','created_at')):
            return blocked('identity_changed', context + ' has a different group identity from the approved evidence. Collect and review the current object.')
    policy = observation.policy_for(finding.environment)
    monitor = matching(finding.evidence, policy.transient_rules)
    if monitor and monitor.mode != 'monitor':
        return blocked('transient_suppression', 'The matching transient-group policy suppresses this finding.')
    days = monitor.empty_days if monitor else policy.empty_group_days
    minimum = monitor.minimum_observations if monitor else policy.minimum_observations
    hours = monitor.maximum_gap_hours if monitor else policy.maximum_gap_hours
    gap = timedelta(hours=hours) if hours else timedelta(minutes=max(1440,2*finding.environment.sync_interval_minutes))
    signature = hashlib.sha256(json.dumps([days, minimum, gap.total_seconds()] +
        ([monitor.pk, monitor.tag_scope] if monitor else [])).encode()).hexdigest()
    if finding.policy_fingerprint != signature:
        return blocked('criteria_changed', 'The current empty-group criteria differ from the approved observation criteria. Complete finding recalculation and review current evidence.')
    if not finding.observation_started:
        return blocked('missing_observation_history', 'The approved finding has no observation start time.')
    if finding.last_seen != source.generated_at:
        return blocked('history_timestamp_mismatch', 'The last verified observation does not match the retained approved collection.')
    if timezone.now()-finding.last_seen > gap:
        return blocked('history_gap_exceeded', f'The last verified observation ({finding.last_seen.isoformat()}) exceeds the configured maximum collection gap ({gap.total_seconds()/3600:g} hours). Obtain fresh verified evidence.')
    if days and (finding.observation_days < days or finding.observation_count < minimum):
        return blocked('insufficient_observations', f'Retained history does not meet the required {days} days and {minimum} observations.')
    return source


def strict_group_empty(client, group):
    """Explicit zero from every applicable endpoint; no VM-only fast shortcut."""
    if not collector.vm_fast_eligible(group):
        return False
    for endpoint in ('virtual-machines','ip-addresses','logical-ports'):
        page = client.get(group['path']+'/members/'+endpoint, {'page_size':1})
        if (not isinstance(page, dict) or page.get('results') != []
                or type(page.get('result_count')) is not int or page['result_count'] != 0
                or page.get('cursor') or any(page.get(key) for key in ('error','error_code','error_message'))):
            return False
    return True
