"""Bounded per-object observation state; never infer continuity from first_seen."""
import hashlib
import json
from datetime import timedelta
from django.utils.dateparse import parse_datetime
from .models import FindingPolicy

KINDS = ('zero_hits', 'empty_group', 'unused', 'empty_policy', 'disabled')
FIELDS = ['qualification', 'observation_started', 'observation_count', 'observation_days',
          'required_days', 'qualified_at', 'qualification_reason', 'policy_fingerprint']


def policy_for(environment):
    return (FindingPolicy.objects.filter(environment=environment).first()
            or FindingPolicy.objects.filter(scope='global').first() or FindingPolicy())


def condition_fingerprint(kind, evidence):
    """Continuity is separate from the full evidence used for review decisions."""
    keys = ['unique_id', 'created_at', 'rule_id', 'policy_rule_id']
    keys += {
        'empty_group': ['membership_definition'],
        'unused': ['configuration'],
        'zero_hits': ['configuration_fingerprint', 'configuration', 'action', 'disabled',
                      'source_groups', 'destination_groups', 'services', 'scope'],
        'disabled': ['configuration_fingerprint', 'configuration', 'action',
                     'source_groups', 'destination_groups', 'services', 'scope'],
        'empty_policy': ['configuration'],
    }.get(kind, [])
    selected = {key: evidence[key] for key in keys if key in evidence}
    if isinstance(selected.get('membership_definition'), dict):
        selected['membership_definition'] = selected['membership_definition'].get('definition', selected['membership_definition'])
    return hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def advance(finding, policy, environment, stamp, reset=False, row=None):
    days = getattr(policy, finding.kind + '_days', None)
    finding.required_days = days or 0
    if days is None:
        finding.qualification = 'insufficient'
        finding.qualification_reason = 'Collection coverage requires review; no inactivity period can be established.'
        finding.observation_started = None
        finding.observation_count = finding.observation_days = 0
        finding.qualified_at = None
        finding.policy_fingerprint = ''
        return

    gap = timedelta(hours=policy.maximum_gap_hours) if policy.maximum_gap_hours else timedelta(minutes=max(1440, 2 * environment.sync_interval_minutes))
    signature = hashlib.sha256(json.dumps([days, policy.minimum_observations, gap.total_seconds()]).encode()).hexdigest()
    restart = (reset or not finding.observation_started or finding.policy_fingerprint != signature
               or stamp <= finding.last_seen or stamp - finding.last_seen > gap)
    if finding.kind == 'zero_hits':
        row = row or {}
        try:
            checked = parse_datetime(row.get('statistics_checked_at') or '')
            fresh = (checked is not None and checked.tzinfo is not None and checked <= stamp
                     and stamp - checked <= gap and (reset or checked > finding.last_seen))
        except (TypeError, ValueError):
            fresh = False
        if not fresh or row.get('hit_count') != 0:
            finding.qualification = 'insufficient'
            finding.qualification_reason = 'Fresh zero-hit statistics are required; missing or repeated counters do not count.'
            finding.observation_started = None
            finding.observation_count = finding.observation_days = 0
            finding.qualified_at = None
            return
    finding.policy_fingerprint = signature
    if restart:
        finding.observation_started = stamp
        finding.observation_count = 0
        finding.qualified_at = None
    finding.observation_count += 1
    finding.observation_days = max(0, (stamp - finding.observation_started).days)
    if days == 0 or (finding.observation_days >= days and finding.observation_count >= policy.minimum_observations):
        finding.qualification = 'eligible'
        finding.qualified_at = finding.qualified_at or stamp
        finding.qualification_reason = ('No waiting period configured; condition confirmed in this collection.'
            if days == 0 else 'Observation period and minimum successful observations satisfied.')
    else:
        finding.qualification = 'observing'
        finding.qualification_reason = ('Observation restarted after a gap exceeding {:.1f} hours. '.format(gap.total_seconds()/3600) if stamp - finding.last_seen > gap else '') + 'Observed {} of {} required days; {} of {} required successful observations.'.format(finding.observation_days, days, finding.observation_count, policy.minimum_observations)


def absent(finding, rows):
    """Only affirmative opposite evidence clears a condition; missing != resolved."""
    row = rows.get(finding.path, {})
    if row.get('audit_exclusions'): row = {}
    clear = {
        'empty_group': row.get('membership') in ('non_empty', 'nonempty'),
        'unused': row.get('usage') == 'referenced',
        'zero_hits': row.get('hit_status') == 'traffic_recorded',
        'disabled': row.get('disabled') is False,
        'empty_policy': isinstance(row.get('rule_count'), int) and row['rule_count'] > 0,
    }.get(finding.kind, False)
    finding.qualification = 'cleared' if clear else 'insufficient'
    finding.qualification_reason = ('Positive evidence cleared the condition.' if clear else
                                   'Missing, excluded or unknown evidence; continuity is not established.')
    finding.observation_started = None
    finding.observation_count = finding.observation_days = 0
    finding.qualified_at = None


def summary(finding):
    return {'kind': finding.kind, 'status': finding.qualification,
            'label': finding.get_qualification_display(), 'reason': finding.qualification_reason,
            'observed_days': finding.observation_days, 'required_days': finding.required_days,
            'observations': finding.observation_count,
            'started_at': finding.observation_started.isoformat() if finding.observation_started else None,
            'qualified_at': finding.qualified_at.isoformat() if finding.qualified_at else None,
            'evaluated_at': finding.evaluated_at.isoformat()}
