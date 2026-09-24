"""Conservative longitudinal counter evidence from database snapshots."""
from collections import Counter
from datetime import timedelta
from django.utils.dateparse import parse_datetime
from django.utils import timezone

GAP_LIMIT = timedelta(hours=24)
COUNTERS = ('hit_count', 'packet_count', 'byte_count', 'session_count')


def identity(rule):
    return tuple(rule.get(key) for key in ('rule_id', 'policy_rule_id', 'unique_id', 'created_at'))


def configuration(rule):
    return rule.get('configuration_fingerprint')


def counters(rule, generated):
    """Require explicit, valid counters and a timestamp; never infer zero from absence."""
    try:
        stamp = parse_datetime(rule.get('statistics_checked_at', ''))
        if not stamp or timezone.is_naive(stamp) or stamp > generated:
            return None
        samples = rule.get('statistics')
        if not isinstance(samples, list) or not samples:
            return None
        result = {}
        for sample in samples:
            scope = sample.get('enforcement_point')
            if not isinstance(scope, str) or not scope or scope in result:
                return None
            values = {key: sample[key] for key in COUNTERS if key in sample}
            if 'hit_count' not in values or any(type(v) is not int or v < 0 for v in values.values()):
                return None
            result[scope] = values
        if rule.get('hit_status') not in ('traffic_recorded', 'zero_hits'):
            return None
        return stamp, result
    except (TypeError, ValueError, AttributeError):
        return None


def analyze(environment, days, end):
    start = end - timedelta(days=days)
    latest = environment.snapshots.filter(testing=False, generated_at__lte=end).values('report__dfw', 'generated_at').first()
    if not latest:
        return {'rows': [], 'counts': {}, 'snapshots': 0, 'failed_jobs': 0, 'start': start, 'end': end}
    latest_rules = (latest['report__dfw'] or {}).get('rules', [])
    states = {r['path']: {'rule': r, 'samples': {}, 'conflicts': set(), 'unknown': 0, 'closed': False, 'changed': False,
                         'limited_identity': not bool(r.get('unique_id') or r.get('created_at')),
                         'limited_config': not bool(configuration(r))} for r in latest_rules}
    baseline = environment.snapshots.filter(testing=False, generated_at__lt=start,
        generated_at__gte=start-GAP_LIMIT).only('pk').first()
    from django.db.models import Q
    window = Q(generated_at__gte=start)
    if baseline:
        window |= Q(pk=baseline.pk)
    snapshots = environment.snapshots.filter(window, testing=False, generated_at__lte=end).values(
        'report__dfw', 'generated_at').order_by('-generated_at', '-created_at')
    snapshot_count = 0
    for snapshot in snapshots.iterator(chunk_size=10):
        snapshot_count += 1
        dfw = snapshot['report__dfw'] or {}
        records = {r['path']: r for r in dfw.get('rules', [])}
        for path, state in states.items():
            if state['closed']:
                continue
            rule = records.get(path)
            if rule is None or identity(rule) != identity(state['rule']):
                state.update(closed=True, changed=True)
                continue
            if configuration(rule) != configuration(state['rule']):
                state.update(closed=True, changed=True)
                continue
            sample = counters(rule, snapshot['generated_at'])
            if dfw.get('errors') or sample is None:
                state['unknown'] += 1
                continue
            stamp, values = sample
            if stamp < start-GAP_LIMIT or stamp > end:
                state['unknown'] += 1
                continue
            # Conflicting imports at the same observation time cannot become zero evidence.
            if stamp in state['conflicts']:
                continue
            if stamp in state['samples'] and state['samples'][stamp] != values:
                state['unknown'] += 1
                state['conflicts'].add(stamp)
                state['samples'].pop(stamp)
                continue
            state['samples'][stamp] = values
    failed_jobs = environment.jobs.filter(status='failed', testing=False,
        finished_at__gte=start, finished_at__lte=end).count()
    rows = []
    for state in states.values():
        rule = state['rule']
        samples = sorted(state['samples'].items())
        stamps = [stamp for stamp, _ in samples]
        positive = [(stamp, values) for stamp, values in samples
                    if any(v > 0 for counts in values.values() for v in counts.values())]
        resets = scope_changes = 0
        for (_, before), (_, after) in zip(samples, samples[1:]):
            if before.keys() != after.keys() or any(before[k].keys() != after[k].keys() for k in before):
                scope_changes += 1
            elif any(after[scope][key] < value for scope in before for key, value in before[scope].items()):
                resets += 1
        boundaries = sorted(set([start, end] + [max(start, stamp) for stamp in stamps]))
        max_gap = max((b-a for a,b in zip(boundaries, boundaries[1:])), default=end-start)
        covers_window = bool(stamps) and stamps[0] <= start and end-stamps[-1] <= GAP_LIMIT
        reasons = []
        if state['limited_identity']: reasons.append('Stable rule identity was not recorded.')
        if state['limited_config']: reasons.append('Legacy snapshot: full rule configuration was not recorded.')
        if state['changed']: reasons.append('Rule changed, was recreated, or was absent; earlier observations excluded.')
        if state['unknown']: reasons.append(f"{state['unknown']} incomplete counter observation(s).")
        if failed_jobs: reasons.append(f'{failed_jobs} failed collection(s) in this window.')
        if scope_changes: reasons.append('Returned enforcement points or counter fields changed.')
        if resets: reasons.append(f'{resets} possible counter reset(s): counters decreased.')
        if len(samples) < 2: reasons.append('At least two distinct counter observations are required.')
        if not covers_window: reasons.append('Observations do not cover both ends of the selected period.')
        if max_gap > GAP_LIMIT: reasons.append('Observation gap exceeds 24 hours.')
        if rule.get('disabled'):
            status = 'disabled'
        elif positive:
            status = 'traffic'
        elif samples:
            status = 'limited' if reasons else 'zero'
        else:
            status = 'unknown'
        rows.append({'name': rule.get('name', rule['path']), 'path': rule['path'], 'rule_id': rule.get('rule_id'),
            'status': status, 'observations': len(samples), 'zero_observations': len(samples)-len(positive),
            'positive_observations': len(positive), 'first': stamps[0] if stamps else None,
            'last': stamps[-1] if stamps else None, 'last_positive': positive[-1][0] if positive else None,
            'max_gap_hours': round(max_gap.total_seconds()/3600, 1), 'reasons': reasons,
            'span_days': round((stamps[-1]-stamps[0]).total_seconds()/86400, 1) if stamps else 0})
    return {'rows': rows, 'counts': dict(Counter(r['status'] for r in rows)), 'snapshots': snapshot_count,
            'failed_jobs': failed_jobs, 'start': start, 'end': end, 'latest': latest['generated_at']}


STATUS_LABELS = {'zero': 'Consistently zero · review candidate', 'limited': 'Zero observations · limited coverage',
                 'traffic': 'Traffic recorded', 'unknown': 'No valid observations', 'disabled': 'Disabled'}
