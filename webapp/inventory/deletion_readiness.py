"""Narrow execution-only replacement for incomplete global VM inventory."""
from datetime import timedelta
import hashlib
import json
from django.utils import timezone
from . import collector, observation
from .evidence_coverage import snapshot_issues, relevant
from .models import Snapshot, SnapshotRecord
from .transient import matching


def historical_group_evidence(finding, latest):
    """Return a fresh approved source only when every intervening gap is VM-only.

    Does not qualify a finding, add observations, or authorize a DELETE. The caller
    must still verify all approvals, live identity, membership and references.
    """
    config = finding.evidence.get('configuration') or {}
    if finding.kind != 'empty_group' or finding.workflow_state != 'ready' or not collector.vm_fast_eligible(config):
        return None
    source = Snapshot.objects.filter(pk=finding.snapshot_id, environment_id=finding.environment_id,
                                     testing=False, imported=False).only('pk','generated_at','summary').first()
    if not source or not latest or source.generated_at >= latest.generated_at:
        return None
    if relevant(snapshot_issues(source), finding.kind, finding.path):
        return None
    # Never bypass a newer verified snapshot, an unknown warning, or another failed area.
    intermediate = finding.environment.snapshots.filter(testing=False, imported=False,
        generated_at__gt=source.generated_at).only('pk','generated_at','summary')
    for snapshot in intermediate:
        entries = snapshot_issues(snapshot)
        if not entries or any(entry.get('endpoint') != '/infra/realized-state/virtual-machines'
                              or entry.get('scope') != 'VM inventory' for entry in entries):
            return None
        saved = SnapshotRecord.objects.filter(snapshot=snapshot, view='inventory', compact__path=finding.path).values('compact', 'data__configuration').first()
        row = saved['compact'] if saved else None
        if (not row or row.get('audit_exclusions') or row.get('membership') != 'empty'
                or saved['data__configuration'] != config
                or any(row.get(key) != finding.evidence.get(key) for key in ('unique_id','created_at'))):
            return None
    policy = observation.policy_for(finding.environment)
    monitor = matching(finding.evidence, policy.transient_rules)
    if monitor and monitor.mode != 'monitor':
        return None
    days = monitor.empty_days if monitor else policy.empty_group_days
    minimum = monitor.minimum_observations if monitor else policy.minimum_observations
    hours = monitor.maximum_gap_hours if monitor else policy.maximum_gap_hours
    gap = timedelta(hours=hours) if hours else timedelta(minutes=max(1440,2*finding.environment.sync_interval_minutes))
    signature = hashlib.sha256(json.dumps([days, minimum, gap.total_seconds()] +
        ([monitor.pk, monitor.tag_scope] if monitor else [])).encode()).hexdigest()
    if (finding.policy_fingerprint != signature or not finding.observation_started
            or finding.last_seen != source.generated_at or timezone.now()-finding.last_seen > gap
            or (days and (finding.observation_days < days or finding.observation_count < minimum))):
        return None
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
