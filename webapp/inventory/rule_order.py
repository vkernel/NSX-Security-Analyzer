"""All saved DFW rules of one snapshot in configured NSX order, one bounded page at a time.

Order and per-field values reuse the firewall reference context, so this list and the
reference drawers agree. Saved configuration only; never effective policy or live NSX.
"""
import json
from django.db import connection

from .firewall_reference_context import enrich, nsx_order_sql, CATEGORY_ORDER

PAGE_SIZE = 50
ACTIONS = ('ALLOW', 'DROP', 'REJECT', 'JUMP_TO_APPLICATION')


def read(snapshot_id, page=0, *, query='', category='', action='', policy=''):
    if page < 0 or page > 100000:
        raise ValueError('Invalid rule page.')
    query = (query or '').strip()[:200]
    if category and category not in CATEGORY_ORDER:
        raise ValueError('Invalid category.')
    if action and action not in ACTIONS:
        raise ValueError('Invalid action.')
    order_select, order = nsx_order_sql('data', 'ordinal')
    conditions, params = ["snapshot_id = %s", "view = 'dfw'", "data #> '{configuration,rule}' IS NOT NULL", "data #> '{configuration,rule}' <> 'null'::jsonb"], [snapshot_id]
    if query:
        conditions.append("(name ILIKE %s OR data->>'rule_id' = %s OR compact->>'path' ILIKE %s)")
        like = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        params += [like, query, like]
    if category:
        conditions.append("lower(data->>'category') = lower(%s)")
        params.append(category)
    if policy:
        conditions.append("data->>'policy_path' = %s")
        params.append(policy[:2000])
    if action:
        conditions.append("upper(data->>'action') = %s")
        params.append(action)
    # Window counts in the order object cover the filtered set, before paging.
    sql = f"""SELECT jsonb_build_object('path', compact->>'path', 'name', name, 'ordinal', ordinal,
                'nsx_order', {order_select})
              FROM inventory_snapshotrecord WHERE {' AND '.join(conditions)}
              ORDER BY {order} LIMIT %s OFFSET %s"""
    with connection.cursor() as cursor:
        cursor.execute(sql, [*params, PAGE_SIZE + 1, page * PAGE_SIZE])
        rows = [json.loads(value) if isinstance(value, str) else value for (value,) in cursor.fetchall()]
        cursor.execute(f"SELECT count(*) FROM inventory_snapshotrecord WHERE {' AND '.join(conditions)}", params)
        total = cursor.fetchone()[0]
    items = enrich(snapshot_id, rows[:PAGE_SIZE])
    # Only rule configuration is shown here; drop reference-specific annotations.
    keep = ('path', 'name', 'ordinal', 'nsx_order', 'category', 'policy_name', 'rule_id', 'action', 'disabled',
            'sequence_number', 'rule_fields', 'show_context_profiles')
    items = [{key: item[key] for key in keep if key in item} for item in items]
    return {'items': items, 'page': page, 'has_next': len(rows) > PAGE_SIZE, 'total': total, 'page_size': PAGE_SIZE}
