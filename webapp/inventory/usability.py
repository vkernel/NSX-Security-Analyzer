"""Workspace presentation helpers; source audit snapshots remain immutable."""
from datetime import timedelta
from django.utils import timezone
from .models import WorkspacePolicy


def policy():
    return WorkspacePolicy.objects.filter(pk=1).first() or WorkspacePolicy(pk=1)


def freshness(environment, settings=None):
    if not environment.enabled:
        return {'label': 'Paused', 'stale': False}
    settings = settings or policy()
    last = environment.snapshots.filter(testing=False, imported=False, job__status='succeeded').order_by('-generated_at').values_list('generated_at', flat=True).first()
    if last is None:
        return {'label': 'No successful full collection', 'stale': True, 'threshold': settings.stale_hours}
    stale = last < timezone.now() - timedelta(hours=settings.stale_hours)
    return {'label': 'Stale data' if stale else 'Up to date', 'stale': stale, 'last': last, 'threshold': settings.stale_hours}


def coverage_keys(report):
    import hashlib
    import json
    items = []
    for row in report.get('objects', []):
        if row.get('membership') == 'unknown':
            items.append(['membership', row.get('path')])
    dfw = report.get('dfw', {})
    items.extend(['dfw-statistics', row.get('path')] for row in dfw.get('rules', []) if row.get('hit_status') == 'unknown')
    items.extend(['dfw-policy', row.get('path')] for row in dfw.get('policies', []) if row.get('status') == 'unknown')
    for name, block in [('dfw', dfw), ('tags', report.get('tags', {}))]:
        items.extend([name, error] for error in block.get('errors', []))
    tags = report.get('tags', {})
    items.extend(['tag-review', row.get('path')] for row in tags.get('objects', []) if row.get('status') == 'unknown')
    if tags.get('unsupported_conditions'):
        items.append(['unsupported-tag-conditions'])
    return sorted({hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest() for item in items})
