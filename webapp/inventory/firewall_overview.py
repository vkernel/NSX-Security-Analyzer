"""Firewall Summary from saved evidence only.

Figures come from the collection-time snapshot summary (``summary.firewall``).
Snapshots published before that projection existed fall back to computing the
same figures once from the saved firewall index rows (never the report JSON). Coverage comes from the
coverage index and changes only from an already calculated comparison. Nothing
here contacts NSX or starts a comparison.
"""
from django.core.cache import cache
from django.urls import reverse

from .collector import DFW_COUNTER_NOTE, dfw_figures
from .models import Snapshot
from .overview_summary import changes as snapshot_changes, coverage as snapshot_coverage

FIREWALL_AREA = 'Firewall policies and rules'


def figures(snapshot):
    saved = Snapshot.objects.filter(pk=snapshot.pk).values_list('summary__firewall', flat=True).get()
    if isinstance(saved, dict) and saved.get('version') == 1:
        return saved
    # Older snapshots: compute the same figures once from the saved firewall index rows
    # (never the report JSON), then reuse; a snapshot's saved evidence never changes.
    return cache.get_or_set(f'firewall-figures:v1:{snapshot.pk}', lambda: from_index(snapshot), 6 * 3600)


RULE_FIELDS = ('action', 'category', 'disabled', 'hit_status', 'source_groups', 'destination_groups', 'services', 'scope')


def from_index(snapshot):
    from .models import SnapshotRecord
    rows = SnapshotRecord.objects.filter(snapshot_id=snapshot.pk, view='dfw')
    rules = [{field: row['data__' + field] for field in RULE_FIELDS}
             for row in rows.filter(compact__has_key='action').values(*('data__' + field for field in RULE_FIELDS))]
    policies = [{'category': row['compact__category'], 'status': row['compact__status']}
                for row in rows.filter(compact__has_key='rule_count').values('compact__category', 'compact__status')]
    # Firewall collection errors are recorded in the collection-time coverage summary.
    coverage = Snapshot.objects.filter(pk=snapshot.pk).values_list('summary__evidence_coverage', flat=True).get() or []
    errors = [entry for entry in coverage if isinstance(entry, dict) and entry.get('scope') in ('dfw', 'Firewall rule inventory')]
    return dfw_figures({'rules': rules, 'policies': policies, 'errors': errors}, snapshot.testing)


def percent(part, whole):
    return round(100 * part / whole, 1) if whole else 0


def firewall_overview(snapshot):
    data = figures(snapshot)
    link = reverse('snapshot', args=[snapshot.pk]) + '?browse=1#'
    enabled, allows = data['enabled'], data['allows']
    cards = [
        {'label': 'Policies', 'value': data['policies'], 'url': link + 'dfw-policies',
         'note': f"{data['empty_policies']} empty" if data['empty_policies'] else 'None empty'},
        {'label': 'Rules', 'value': data['rules'], 'url': link + 'dfw-rules',
         'note': f"{enabled} enabled · {data['disabled']} disabled"},
        {'label': 'Recording traffic', 'value': data['traffic'], 'url': link + 'dfw-rules',
         'note': f'{percent(data["traffic"], enabled)}% of enabled rules'},
        {'label': 'Zero recorded hits', 'value': data['zero_hits'], 'url': link + 'zero-hit-rules',
         'note': 'Enabled rules; current counters'},
        {'label': 'Unknown activity', 'value': data['unknown_activity'], 'url': link + 'unknown-statistics',
         'note': 'Counters not available' if data['unknown_activity'] else 'All enabled rules assessed'},
    ]
    if data['incomplete']:
        segmentation = {'state': 'not_assessed', 'label': 'Not assessed'}
    elif not allows:
        segmentation = {'state': 'na', 'label': 'N/A'}
    else:
        value = percent(data['bounded'], allows)
        segmentation = {'state': 'assessed', 'label': f'{value}%', 'value': value}
    review = [dict(prompt, url=link + 'dfw-rules') for prompt in data['prompts']] + [
        {'key': 'disabled', 'label': 'Disabled rules', 'count': data['disabled'], 'url': link + 'disabled-rules'},
        {'key': 'empty_policies', 'label': 'Empty policies', 'count': data['empty_policies'], 'url': link + 'empty-policies'}]
    largest = max([item['count'] for item in data['actions']] or [0])
    actions = [dict(item, width=round(100 * item['count'] / largest) if largest else 0) for item in data['actions']]
    coverage = snapshot_coverage(snapshot)
    firewall_rows = [row for row in coverage['rows'] if row['area'] == FIREWALL_AREA]
    issues = firewall_rows[0]['detail'] if firewall_rows and firewall_rows[0]['state'] == 'Needs review' else ''
    change = snapshot_changes(snapshot)
    if change.get('ready'):
        change['kinds'] = [kind for kind in change['kinds'] if kind['label'] == 'Firewall rules']
    return {'cards': cards, 'segmentation': segmentation, 'data': data, 'review': review, 'actions': actions,
            'coverage': {'recorded': coverage['recorded'], 'issues': issues,
                         'needs_review': bool(issues) or data['incomplete'] or not coverage['recorded']},
            'changes': change, 'rules_url': link + 'dfw-rules', 'counter_note': DFW_COUNTER_NOTE}
