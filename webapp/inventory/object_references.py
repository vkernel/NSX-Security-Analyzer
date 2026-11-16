"""Bounded references for saved groups/services, shared with workload firewall tables."""
import json
from django.db import connection
from django.http import Http404
from .models import SnapshotRecord
from .firewall_reference_context import enrich, nsx_order_sql


def read(snapshot_id, path, section='firewall', page=0, *, full=False):
    if section not in ('firewall', 'objects') or page < 0 or page > 1000000:
        raise ValueError('Invalid object reference page or category.')
    owner = SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='inventory', compact__path=path).values('pk', 'data__kind').first()
    if owner is None:
        raise Http404('Object is not available in this saved snapshot.')
    kind = owner['data__kind']
    if kind not in ('group', 'custom_service'):
        raise ValueError('References are available for groups and custom services.')
    size=500 if full else 25
    source = """WITH source AS (
            SELECT data->'referenced_by' AS refs, data->'reference_details' AS details
            FROM inventory_snapshotrecord WHERE id = %s), paths AS (
            SELECT DISTINCT value AS path FROM source,
                jsonb_array_elements_text(COALESCE(refs, '[]'::jsonb))
            WHERE (position('/rules/' in value) > 0) = %s{policy_rules})"""
    name = """(SELECT item->>'name' FROM source, jsonb_array_elements(COALESCE(details, '[]'::jsonb)) item
                WHERE item->>'path' = paths.path LIMIT 1)"""
    # NSX applies a policy's Applied To to every rule in it, so those rules reference the group
    # too. Saved references attribute that field to the policy path, not to its rules.
    policy_rules = """ UNION SELECT compact->>'path' FROM inventory_snapshotrecord
            WHERE snapshot_id = %s AND view = 'dfw' AND data#>'{configuration,policy,scope}' ? %s""" if section == 'firewall' and kind == 'group' else ''
    source = source.format(policy_rules=policy_rules)
    extra = [snapshot_id, path] if policy_rules else []
    with connection.cursor() as cursor:
        if section == 'firewall':
            # Saved rule records give the configured NSX order before paging.
            order_select, order = nsx_order_sql('saved_rule.data', 'paths.path')
            cursor.execute(source + f""" SELECT jsonb_build_object('path', paths.path, 'name', {name},
                    'via_group', %s, 'nsx_order', {order_select})
                FROM paths LEFT JOIN inventory_snapshotrecord saved_rule ON saved_rule.snapshot_id = %s AND saved_rule.view = 'dfw'
                    AND saved_rule.compact->>'path' = paths.path
                ORDER BY {order} LIMIT %s OFFSET %s""", [owner['pk'], True, *extra, path, snapshot_id, size+1, page*size])
        else:
            cursor.execute(source + f""" SELECT jsonb_build_object('path', paths.path, 'name', {name}, 'via_group', %s)
                FROM paths ORDER BY paths.path LIMIT %s OFFSET %s""", [owner['pk'], False, path, size+1, page*size])
        rows = [json.loads(value) if isinstance(value, str) else value for (value,) in cursor.fetchall()]
    items = rows[:size]
    if section == 'firewall':
        items = enrich(snapshot_id, items, object_kind=kind,full=full)
    return {'items': items, 'page': page, 'has_next': len(rows) > size}
