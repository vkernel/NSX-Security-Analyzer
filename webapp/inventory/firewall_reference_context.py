"""Enrich one bounded reference page from the same saved snapshot, never live NSX."""
from .models import SnapshotRecord

FIELDS = (('source_groups', 'Source'), ('destination_groups', 'Destination'), ('scope', 'Applied to'))


def enrich(snapshot_id, items, vm=False, object_kind=None):
    if not items:
        return items
    paths = {item.get('path') for item in items if item.get('path')}
    keys = ('category', 'policy_name', 'rule_id', 'action', 'disabled', 'sequence_number',
            'source_groups', 'destination_groups', 'scope')
    records = SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='dfw', compact__path__in=paths).values(
        'compact__path', 'data__name', 'data__services', 'data__configuration__rule__sequence_number',
        'data__configuration__rule__sources_excluded','data__configuration__rule__destinations_excluded', *['data__' + key for key in keys])
    rules = {row['compact__path']: row for row in records}
    group_paths = set()
    for item in items:
        groups = item.get('via_groups', []) if vm else [item.get('via_group')]
        group_paths.update(path for path in groups[:25] if isinstance(path, str))
    names = dict(SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='inventory', compact__path__in=group_paths)
                 .values_list('compact__path', 'name')) if group_paths else {}
    result = []
    for item in items:
        row = dict(item)
        saved = rules.get(row.get('path'), {})
        for key in keys[:6]:
            value = saved.get('data__' + key)
            if value is not None:
                row[key] = value
        if saved.get('data__name'):
            row['name'] = saved['data__name']
        sequence = saved.get('data__configuration__rule__sequence_number')
        if sequence is not None:
            row['sequence_number'] = sequence
        # The local snapshot's dfw view contains distributed-firewall rules only.
        row['firewall_type'] = 'DFW' if saved else row.get('firewall_type')
        groups = row.get('via_groups', []) if vm else [row.get('via_group')]
        row['groups_truncated'] = len(groups) > 25
        row['matched_groups'] = [{'path': path, 'name': names.get(path, path.rsplit('/', 1)[-1])}
                                 for path in groups[:25] if isinstance(path, str)]
        matched, field_matches = [], []
        valid_groups = row['matched_groups']
        fields = (('services', 'Service'),) if object_kind == 'custom_service' else FIELDS
        for key, label in fields:
            configured = saved.get('data__' + key)
            if not isinstance(configured, list):
                continue
            direct = [group for group in valid_groups if group['path'] in configured]
            if direct:
                excluded = saved.get('data__configuration__rule__'+('sources_excluded' if key=='source_groups' else 'destinations_excluded' if key=='destination_groups' else '')) is True
                field=label+' (excluded)' if excluded else label
                matched.append(field)
                field_matches.extend({'field':field,'group':group,'excluded':excluded} for group in direct)
                if excluded:row['exclusion_reference']=True
        row['matched_fields'] = matched
        row['field_matches'] = field_matches
        row['reference_reason'] = ('Saved service reference' if object_kind == 'custom_service' else
                                   'Saved group reference' if object_kind == 'group' else
                                   'Tag condition; VM membership unverified' if vm else
                                   'Group membership condition' if row.get('tag_use') == 'condition' else
                                   'Tag attached to group (metadata)' if row.get('tag_use') == 'assignment' else
                                   'Relationship reason not retained')
        direct_paths = {match['group']['path'] for match in field_matches}
        unresolved = any(group['path'] not in direct_paths for group in valid_groups)
        row['field_note'] = ('Direct field not retained for some references; they may pass through a nested group'
                             if unresolved or not valid_groups else '')
        result.append(row)
    return result
