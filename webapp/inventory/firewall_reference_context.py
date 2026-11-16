"""Enrich one bounded reference page from the same saved snapshot, never live NSX."""
from functools import lru_cache
from .models import SnapshotRecord

FIELDS = (('source_groups', 'Source'), ('destination_groups', 'Destination'), ('scope', 'Applied to'))
# Saved rule configuration shown per reference row. Values are configuration, not effective policy.
RULE_FIELDS = (('sources', 'data__source_groups', 'data__configuration__rule__sources_excluded'),
               ('destinations', 'data__destination_groups', 'data__configuration__rule__destinations_excluded'),
               ('services', 'data__services', None),
               ('context_profiles', 'data__configuration__rule__profiles', None),
               ('applied_to', 'data__scope', None))
FIELD_LIMIT = 25
# NSX evaluates DFW categories in this order: Ethernet (L2), then the layer-3 categories.
CATEGORY_ORDER = ('Ethernet', 'Emergency', 'Infrastructure', 'Environment', 'Application')


def nsx_order_sql(data, tiebreak):
    """Configured NSX order for a saved dfw `data` column, applied before paging.

    Returns (select, order). `select` is a jsonb object with the category/policy keys,
    totals and whether the row continues the previous row's group (window functions
    run before LIMIT/OFFSET, so counts and continuation span every page). Only fixed
    SQL text is produced here; `data`/`tiebreak` are trusted column expressions.
    """
    number = lambda expr: f"(CASE WHEN jsonb_typeof({expr}) = 'number' THEN ({expr})::numeric END)"
    category = f"COALESCE(NULLIF({data}->>'category', ''), 'Not recorded')"
    rank = ("(CASE lower(" + category + ") " +
            ' '.join(f"WHEN '{name.lower()}' THEN {index}" for index, name in enumerate(CATEGORY_ORDER)) +
            f" ELSE {len(CATEGORY_ORDER)} END)")
    policy_sequence = number(f"{data}#>'{{configuration,policy,sequence_number}}'")
    rule_sequence = number(f"COALESCE({data}#>'{{configuration,rule,sequence_number}}', {data}->'sequence_number')")
    policy = f"COALESCE({data}->>'policy_path', {data}->>'policy_name', '')"
    # Unsaved rules have no name here, so the caller's tiebreak keeps their stored order.
    order = (f"{rank}, {category}, {policy_sequence} NULLS LAST, {policy}, {rule_sequence} NULLS LAST, "
             f"{data}->>'name' NULLS LAST, {tiebreak}")
    window = f"OVER (ORDER BY {order})"
    select = f"""jsonb_build_object('category', {category}, 'category_known', {rank} < {len(CATEGORY_ORDER)},
        'policy', COALESCE({data}->>'policy_name', ''), 'policy_key', {policy},
        'policy_sequence', {policy_sequence}, 'rule_sequence', {rule_sequence},
        'category_count', count(*) OVER (PARTITION BY {category}),
        'policy_count', count(*) OVER (PARTITION BY {category}, {policy}),
        'category_continued', COALESCE(lag({category}) {window} = {category}, false),
        'policy_continued', COALESCE(lag(jsonb_build_array({category}, {policy})) {window} = jsonb_build_array({category}, {policy}), false))"""
    return select, order


def explicit_entries(values):
    """Explicit entries only; ANY or an empty list means no restriction."""
    if not isinstance(values, list):
        return None
    return [value for value in values if isinstance(value, str) and value.upper() != 'ANY']


@lru_cache(maxsize=256)
def context_profiles_in_use(snapshot_id):
    """Snapshot-wide, so the column stays stable across pages and reference drawers."""
    rules = SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='dfw')
    return any(explicit_entries(values) for values in rules.filter(data__configuration__rule__profiles__isnull=False)
               .values_list('data__configuration__rule__profiles', flat=True).iterator(chunk_size=2000))


def rule_field(values, matched, names, limit, excluded=False, origin='rule'):
    explicit = explicit_entries(values)
    if explicit is None:
        return {'recorded': False, 'items': [], 'total': 0, 'any': False, 'excluded': False, 'origin': origin}
    # Matched entries first, so a truncated cell still shows why the rule is listed.
    ordered = sorted(dict.fromkeys(explicit), key=lambda path: path not in matched)
    shown = ordered if limit is None else ordered[:limit]
    return {'recorded': True, 'any': not explicit, 'excluded': bool(excluded and explicit), 'total': len(ordered), 'origin': origin,
            'items': [{'path': path, 'name': names.get(path, path.rsplit('/', 1)[-1]), 'known': path in names,
                       'matched': path in matched} for path in shown]}


def via(item, vm):
    """Matched groups: VMs and merged tag rows carry a list; group/service rows carry one."""
    if vm or isinstance(item.get('via_groups'), list):
        return item.get('via_groups') or []
    return [item.get('via_group')]


def enrich(snapshot_id, items, vm=False, object_kind=None, *, full=False):
    if not items:
        return items
    paths = {item.get('path') for item in items if item.get('path')}
    keys = ('category', 'policy_name', 'rule_id', 'action', 'disabled', 'sequence_number',
            'source_groups', 'destination_groups', 'scope')
    records = SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='dfw', compact__path__in=paths).values(
        'compact__path', 'data__name', 'data__services', 'data__configuration__rule__sequence_number',
        'data__configuration__rule__sources_excluded','data__configuration__rule__destinations_excluded',
        'data__configuration__rule__profiles', 'data__configuration__policy__scope', *['data__' + key for key in keys])
    rules = {row['compact__path']: row for row in records}
    limit = None if full else FIELD_LIMIT
    group_paths = set()
    for item in items:
        groups = via(item, vm)
        group_paths.update(path for path in (groups if full else groups[:25]) if isinstance(path, str))
    for saved in rules.values():
        for _, key, _ in RULE_FIELDS + (('policy_scope', 'data__configuration__policy__scope', None),):
            values = explicit_entries(saved.get(key)) or []
            # Matched paths are already included above; the rest of a cell shows the first entries.
            group_paths.update(values if limit is None else values[:limit])
    names = {}
    group_paths = sorted(group_paths)
    for start in range(0,len(group_paths),5000):
        names.update(SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='inventory', compact__path__in=group_paths[start:start+5000])
                     .values_list('compact__path', 'name'))
    show_profiles = context_profiles_in_use(str(snapshot_id))
    result = []
    for item in items:
        row = dict(item)
        saved = rules.get(row.get('path'), {})
        for key in (keys if full else keys[:6]):
            value = saved.get('data__' + key)
            if value is not None:
                row[key] = value
        if full:
            row['services']=saved.get('data__services')
            row['sources_excluded']=saved.get('data__configuration__rule__sources_excluded')
            row['destinations_excluded']=saved.get('data__configuration__rule__destinations_excluded')
        if saved.get('data__name'):
            row['name'] = saved['data__name']
        sequence = saved.get('data__configuration__rule__sequence_number')
        if sequence is not None:
            row['sequence_number'] = sequence
        # The local snapshot's dfw view contains distributed-firewall rules only.
        row['firewall_type'] = 'DFW' if saved else row.get('firewall_type')
        groups = via(row, vm)
        row['groups_truncated'] = not full and len(groups) > 25
        row['matched_groups'] = [{'path': path, 'name': names.get(path, path.rsplit('/', 1)[-1])}
                                 for path in (groups if full else groups[:25]) if isinstance(path, str)]
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
        policy_scope = explicit_entries(saved.get('data__configuration__policy__scope')) if object_kind != 'custom_service' else None
        if policy_scope and 'Applied to' not in matched:
            # Shown as a match only; the policy's Applied To does not change which rules are listed.
            direct = [group for group in valid_groups if group['path'] in policy_scope]
            if direct:
                matched.append('Applied to (policy)')
                field_matches.extend({'field':'Applied to (policy)','group':group,'excluded':False} for group in direct)
        row['matched_fields'] = matched
        row['field_matches'] = field_matches
        uses = row.get('tag_uses') if isinstance(row.get('tag_uses'), list) else [row.get('tag_use')]
        row['reference_reason'] = ('Saved service reference' if object_kind == 'custom_service' else
                                   'Saved policy Applied To; applies to every rule in the policy' if object_kind == 'group' and matched == ['Applied to (policy)'] else
                                   'Saved group reference' if object_kind == 'group' else
                                   'Tag condition; VM membership unverified' if vm else
                                   'Group membership condition; tag also attached to a group (metadata)' if set(uses) == {'condition', 'assignment'} else
                                   'Group membership condition' if 'condition' in uses else
                                   'Tag attached to group (metadata)' if 'assignment' in uses else
                                   'Relationship reason not retained')
        direct_paths = {match['group']['path'] for match in field_matches}
        unresolved = any(group['path'] not in direct_paths for group in valid_groups)
        row['field_note'] = ('Direct field not retained for some references; they may pass through a nested group'
                             if unresolved or not valid_groups else '')
        if saved:
            matched_paths = {group['path'] for group in valid_groups}
            row['rule_fields'] = {name: rule_field(saved.get(key), matched_paths, names, limit, saved.get(excluded_key) is True if excluded_key else False)
                                  for name, key, excluded_key in RULE_FIELDS}
            # A policy-level Applied To takes precedence over the rule's own value in NSX.
            policy_scope = explicit_entries(saved.get('data__configuration__policy__scope'))
            if policy_scope:
                row['rule_fields']['applied_to'] = rule_field(policy_scope, matched_paths, names, limit, origin='policy')
            row['show_context_profiles'] = show_profiles
        result.append(row)
    return result
