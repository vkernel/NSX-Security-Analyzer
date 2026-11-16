"""Inventory overview summary from saved projections only.

Counts come from the collection-time snapshot summary, coverage from the
collection-time coverage index, and changes only from an already calculated
comparison. Page reads never open report JSON or start a comparison.
"""
from django.db.models import Count, F
from django.db.models.functions import JSONObject
from django.urls import reverse

from .models import Snapshot, SnapshotComparison, SnapshotCoverage

# Coverage issue areas recorded by coverage_index.report_issues, grouped as shown.
COVERAGE_AREAS = (
    ('VM inventory and tags', ('Tag usage', 'TAGS', 'Tags')),
    ('Group membership', ('Group membership',)),
    ('Interface attachments', ('Network references',)),
    ('Firewall policies and rules', ('Policy inventory', 'DFW', 'Rule counters')),
    ('Reference search', ('Search',)),
    ('Inventory verification', ('Inventory verification', 'Audit')),
)
CHANGE_KINDS = (('Group', 'Groups'), ('Service', 'Services'), ('Rule', 'Firewall rules'))


def _number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def counts(snapshot, summary):
    report = reverse('snapshot', args=[snapshot.pk]) + '?browse=1#'
    rules, policies = _number(summary.get('rules')), _number(summary.get('policies'))
    return [
        {'label': 'VMs', 'value': _number(summary.get('vms')), 'note': 'Scanned VM inventory', 'url': report + 'all-vms'},
        {'label': 'Groups', 'value': _number(summary.get('groups')), 'note': 'Scanned; system groups excluded', 'url': report + 'all-groups'},
        {'label': 'Custom services', 'value': _number(summary.get('services')), 'note': 'Built-in services not assessed', 'url': report + 'all-services'},
        {'label': 'Firewall rules', 'value': rules, 'note': f'in {policies} {"policy" if policies == 1 else "policies"}' if policies is not None else 'Policies not recorded', 'url': report + 'dfw-rules'},
        {'label': 'Tags', 'value': _number(summary.get('tags')), 'note': 'Tag and scope pairs', 'url': report + 'tags-all'},
    ]


def coverage(snapshot):
    saved = SnapshotCoverage.objects.filter(snapshot_id=snapshot.pk).first()
    if saved is None:
        # A missing projection is unknown coverage, never complete coverage.
        return {'recorded': False, 'rows': [{'area': area, 'state': 'Not recorded', 'tone': 'neutral', 'detail': ''}
                                            for area, _ in COVERAGE_AREAS]}
    totals = dict(saved.issues.values_list('area').annotate(total=Count('pk')))
    rows, known = [], set()
    for area, sources in COVERAGE_AREAS:
        known.update(sources)
        issues = sum(totals.get(source, 0) for source in sources)
        rows.append({'area': area, 'state': 'Needs review' if issues else 'Complete', 'tone': 'warning' if issues else 'ok',
                     'detail': f'{issues} issue{"s" if issues != 1 else ""}' if issues else ''})
    other = sum(total for area, total in totals.items() if area not in known)
    if other:
        rows.append({'area': 'Other evidence', 'state': 'Needs review', 'tone': 'warning', 'detail': f'{other} issue{"s" if other != 1 else ""}'})
    return {'recorded': True, 'rows': rows, 'issue_count': saved.issue_count}


def changes(snapshot):
    previous = (Snapshot.objects.filter(environment_id=snapshot.environment_id, testing=False, generated_at__lt=snapshot.generated_at)
                .only('pk', 'generated_at').order_by('-generated_at').first())
    if previous is None:
        return {'previous': None}
    compare_url = reverse('snapshot-comparison', args=[snapshot.environment_id]) + f'?before={previous.pk}&after={snapshot.pk}'
    saved = SnapshotComparison.objects.filter(before=previous, after=snapshot, ready=True).first()
    if saved is None:
        return {'previous': previous, 'ready': False, 'url': compare_url}
    totals = {}
    for row in saved.rows.values('data__kind', 'data__status').annotate(total=Count('pk')):
        totals[(row['data__kind'], row['data__status'])] = row['total']
    kinds = [{'label': label, 'added': totals.get((kind, 'Added'), 0), 'removed': totals.get((kind, 'Removed'), 0),
              'changed': totals.get((kind, 'Changed'), 0),
              'uncertain': totals.get((kind, 'Presence uncertain'), 0) + totals.get((kind, 'Evidence incomplete'), 0)}
             for kind, label in CHANGE_KINDS]
    return {'previous': previous, 'ready': True, 'url': compare_url, 'kinds': kinds}


def inventory_overview(snapshot):
    # Extract only the count keys; the full summary can hold thousands of coverage fingerprints.
    keys = ('vms', 'groups', 'services', 'rules', 'policies', 'tags')
    summary = Snapshot.objects.filter(pk=snapshot.pk).annotate(counts=JSONObject(**{key: F('summary__' + key) for key in keys})
                                                               ).values_list('counts', flat=True).get() or {}
    return {'counts': counts(snapshot, summary), 'coverage': coverage(snapshot), 'changes': changes(snapshot),
            'coverage_url': reverse('collection-coverage', args=[snapshot.environment_id])}
