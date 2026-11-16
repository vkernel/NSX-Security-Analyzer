"""Bounded, cached suggestions from retained evidence; never contacts NSX."""
from collections import defaultdict
from django.core.cache import cache
from .models import SnapshotRecord
from .naming import group_evidence


def catalog(snapshot):
    key = 'naming-catalog-v1-' + str(snapshot.pk)
    result = cache.get(key)
    if result is not None:
        return result
    pairs = {}
    truncated = False
    def add(tags, source):
        nonlocal truncated
        for scope, value in {(str(t.get('scope', '')), str(t.get('tag', ''))) for t in tags if isinstance(t, dict)}:
            if not scope or not value:
                continue
            pair = (scope, value)
            if pair not in pairs:
                if len(pairs) >= 10000:
                    truncated = True
                    continue
                pairs[pair] = {'scope': scope, 'value': value, 'groups': 0, 'vms': 0}
            pairs[pair][source] += 1
    scope_groups, scope_vms = defaultdict(int), defaultdict(int)
    for row in SnapshotRecord.objects.filter(snapshot=snapshot, view='inventory', data__kind='group').values_list('data__configuration', flat=True).iterator(chunk_size=500):
        tags = group_evidence(row)['candidate_tags']
        add(tags, 'groups')
        for scope in {t['scope'] for t in tags}:
            scope_groups[scope] += 1
    for tags in SnapshotRecord.objects.filter(snapshot=snapshot, view='vms').values_list('data__tags', flat=True).iterator(chunk_size=500):
        tags = tags if isinstance(tags, list) else []
        add(tags, 'vms')
        for scope in {str(t.get('scope', '')) for t in tags if isinstance(t, dict)}:
            scope_vms[scope] += 1
    result = {'pairs': list(pairs.values()), 'scope_groups': dict(scope_groups), 'scope_vms': dict(scope_vms), 'truncated': truncated}
    cache.set(key, result, 120)
    return result
