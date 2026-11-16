"""Services and tag scopes from one snapshot's saved records, for the overview layouts.

Saved configuration only. "Not referenced" means no saved reference in this snapshot,
not that an object is unused elsewhere; a shared tag never establishes membership.
"""
from django.db.models import Count, Q

from .models import SnapshotRecord

PAGE_SIZE = 50
BROAD_PORTS = 1000


def _ports(values):
    """Port span of one entry; None when a value is not a plain port or range."""
    total = 0
    for value in values or []:
        text = str(value)
        low, _, high = text.partition('-')
        if not low.isdigit() or (high and not high.isdigit()):
            return None
        total += (int(high) - int(low) + 1) if high else 1
    return total


def describe(configuration):
    """Short entry text and observations from a saved service definition."""
    entries, notes = [], []
    for entry in (configuration or {}).get('service_entries') or []:
        kind = entry.get('resource_type', '')
        if kind == 'NestedServiceServiceEntry':
            entries.append((entry.get('nested_service_path') or '').rsplit('/', 1)[-1] or 'Nested service')
            if 'Nested service' not in notes: notes.append('Nested service')
        elif kind == 'L4PortSetServiceEntry':
            protocol, ports = entry.get('l4_protocol') or 'L4', entry.get('destination_ports') or []
            entries.append(protocol + ' ' + (', '.join(map(str, ports)) if ports else 'any port'))
            span = _ports(ports)
            if not ports or (span is not None and span > BROAD_PORTS):
                if 'Broad port range' not in notes: notes.append('Broad port range')
        elif kind == 'ICMPTypeServiceEntry':
            entries.append(entry.get('protocol') or 'ICMP')
        elif kind:
            entries.append(entry.get('display_name') or kind.replace('ServiceEntry', ''))
    return entries, notes


def services(snapshot_id, page=0, *, view='', query=''):
    if page < 0 or page > 100000 or view not in ('', 'custom', 'predefined', 'unreferenced'):
        raise ValueError('Invalid service page or view.')
    base = SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='inventory', data__kind='custom_service')
    predefined = Q(data__configuration__is_default=True) | Q(data__inventory_type='Built-in service')
    unreferenced = Q(data__usage='unused_candidate')
    totals = base.aggregate(total=Count('pk'), predefined=Count('pk', filter=predefined),
                            unreferenced=Count('pk', filter=unreferenced))
    rows = base
    if view == 'custom': rows = rows.exclude(predefined)
    elif view == 'predefined': rows = rows.filter(predefined)
    elif view == 'unreferenced': rows = rows.filter(unreferenced)
    query = (query or '').strip()[:200]
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(compact__path__icontains=query) | Q(search_basic__icontains=query))
    count = rows.count()
    records = list(rows.order_by('sort_name', 'ordinal').values(
        'ordinal', 'name', 'compact__path', 'data__configuration', 'data__referenced_by', 'data__usage',
        'data__inventory_type')[page * PAGE_SIZE:(page + 1) * PAGE_SIZE + 1])
    items = []
    for record in records[:PAGE_SIZE]:
        configuration = record['data__configuration'] or {}
        entries, notes = describe(configuration)
        usage = record['data__usage']
        # Built-in services are not assessed: no reference count is claimed for them.
        references = record['data__referenced_by'] if isinstance(record['data__referenced_by'], list) and usage != 'not_assessed' else None
        builtin = configuration.get('is_default') is True or record['data__inventory_type'] == 'Built-in service'
        if usage == 'unused_candidate': notes.insert(0, 'Not referenced')
        items.append({'ordinal': record['ordinal'], 'name': record['name'], 'path': record['compact__path'],
                      'entries': entries, 'predefined': builtin, 'usage': usage,
                      'rules': sum('/rules/' in path for path in references) if references is not None else None,
                      'references': len(references) if references is not None else None, 'notes': notes})
    return {'items': items, 'page': page, 'has_next': len(records) > PAGE_SIZE, 'count': count,
            'totals': {**totals, 'custom': totals['total'] - totals['predefined']}, 'page_size': PAGE_SIZE}


def scopes(snapshot_id, scope=None):
    rows = list(SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='scopes').order_by('sort_name', 'ordinal').values(
        'name', 'data__tag_count', 'data__vm_count', 'data__group_count', 'data__other_count')[:2000])
    result = {'scopes': [{'name': row['name'], 'values': row['data__tag_count'], 'vms': row['data__vm_count'],
                          'groups': row['data__group_count'], 'other': row['data__other_count']} for row in rows]}
    if scope is None:
        return result
    values = list(SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='tags', data__scope=scope).order_by('sort_name', 'ordinal').values(
        'ordinal', 'name', 'data__vm_count', 'data__group_count', 'data__other_count', 'data__status', 'data__vm_usage', 'data__group_usage')[:1000])
    folded = {}
    for value in values:
        folded.setdefault((value['name'] or '').casefold(), []).append(value['name'])
    items = []
    for value in values:
        notes = []
        variants = [name for name in folded[(value['name'] or '').casefold()] if name != value['name']]
        if variants: notes.append('Case differs from ' + ', '.join(variants))
        if value['data__status'] == 'unknown': notes.append('Needs review')
        elif not value['data__vm_count'] and value['data__group_count']: notes.append('No VMs in snapshot')
        elif value['data__status'] == 'vm_only': notes.append('VM tags only')
        items.append({'ordinal': value['ordinal'], 'value': value['name'], 'vms': value['data__vm_count'],
                      'groups': value['data__group_count'], 'other': value['data__other_count'],
                      'status': value['data__status'], 'notes': notes})
    result.update({'scope': scope, 'values': items, 'truncated': len(values) >= 1000})
    return result
