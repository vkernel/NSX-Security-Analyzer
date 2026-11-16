"""Global search over saved, browsable snapshots. Never contacts NSX.

Each environment contributes its latest full snapshot whose browsing records are
ready. Matching is a case-insensitive name prefix on the indexed ``sort_name``
column, so a query costs one bounded index range scan per environment instead of
scanning every record.
"""
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

from .models import Environment, Snapshot, SnapshotRecord, SnapshotRecordPanel

# Preferred report table for each object kind, in display order.
PANELS = [('all-vms', 'VM'), ('all-groups', 'Group'), ('all-services', 'Service'),
          ('dfw-rules', 'Firewall rule'), ('dfw-policies', 'Firewall policy'),
          ('tags-all', 'Tag'), ('tags-scopes', 'Tag scope')]
PER_ENVIRONMENT = 8
ENVIRONMENT_LIMIT = 10
TOTAL_LIMIT = 20


def latest_browsable(environment_ids):
    """Latest full, browsable snapshot per environment (metadata columns only)."""
    result = {}
    for environment_id in environment_ids:
        snapshot = (Snapshot.objects.filter(environment_id=environment_id, testing=False, browsing_status='ready')
                    .only('pk', 'environment_id', 'generated_at').order_by('-generated_at', '-created_at').first())
        if snapshot:
            result[environment_id] = snapshot
    return result


@login_required
@require_GET
def navigation_search(request):
    query = ' '.join(request.GET.get('q', '').split())[:120]
    if len(query) < 2:
        return JsonResponse({'results': [], 'query': query})
    environments = list(Environment.objects.only('id', 'name').order_by('name')[:ENVIRONMENT_LIMIT])
    requested = request.GET.get('environment', '')
    if requested.isdecimal():
        environments = [env for env in environments if str(env.pk) == requested] or environments
    snapshots = latest_browsable([env.pk for env in environments])
    names = {env.pk: env.name for env in environments}
    prefix = query.casefold()
    results = []
    for environment_id, snapshot in snapshots.items():
        records = list(SnapshotRecord.objects.filter(snapshot_id=snapshot.pk, sort_name__startswith=prefix)
                       .order_by('sort_name').values('pk', 'name')[:PER_ENVIRONMENT])
        if not records:
            continue
        panels = {}
        for record_id, slug in (SnapshotRecordPanel.objects
                                .filter(record_id__in=[row['pk'] for row in records], panel__slug__in=[slug for slug, _ in PANELS])
                                .values_list('record_id', 'panel__slug')):
            panels.setdefault(record_id, set()).add(slug)
        base = reverse('snapshot', args=[snapshot.pk])
        for row in records:
            found = panels.get(row['pk'], set())
            slug, kind = next(((slug, kind) for slug, kind in PANELS if slug in found), ('overview', 'Object'))
            results.append({'type': 'Objects', 'label': row['name'], 'kind': kind,
                            'meta': '{} · {} · saved {}'.format(kind, names.get(environment_id, ''), snapshot.generated_at.strftime('%d %b %H:%M')),
                            'url': base + '?' + urlencode({'browse': '1', 'find': row['name']}) + '#' + slug})
    results.sort(key=lambda item: (item['label'].casefold(), item['meta']))
    return JsonResponse({'results': results[:TOTAL_LIMIT], 'query': query,
                         'limited': len(results) > TOTAL_LIMIT,
                         'source': 'Saved snapshots only; NSX is not contacted.'})
