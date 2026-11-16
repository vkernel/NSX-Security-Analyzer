"""Saved DFW policies of one snapshot in configured NSX order, with rule totals.

Saved configuration only: Applied To, stateful flag and rule counts come from the
snapshot's own policy and rule records, never live NSX or effective policy.
"""
import json
from django.db import connection

from .firewall_reference_context import CATEGORY_ORDER, explicit_entries
from .models import SnapshotRecord

PAGE_SIZE = 100


def read(snapshot_id, page=0):
    if page < 0 or page > 10000:
        raise ValueError('Invalid policy page.')
    category = "COALESCE(NULLIF(policy.data->>'category', ''), 'Not recorded')"
    rank = ("(CASE lower(" + category + ") " +
            ' '.join(f"WHEN '{name.lower()}' THEN {index}" for index, name in enumerate(CATEGORY_ORDER)) +
            f" ELSE {len(CATEGORY_ORDER)} END)")
    sequence = ("(CASE WHEN jsonb_typeof(policy.data#>'{configuration,sequence_number}') = 'number' "
                "THEN (policy.data#>'{configuration,sequence_number}')::numeric END)")
    sql = f"""WITH rules AS (
            SELECT data->>'policy_path' AS policy_path, count(*) AS total,
                   count(*) FILTER (WHERE data->'disabled' = 'true'::jsonb) AS disabled
            FROM inventory_snapshotrecord
            WHERE snapshot_id = %s AND view = 'dfw' AND data #> '{{configuration,rule}}' IS NOT NULL
            GROUP BY 1)
        SELECT jsonb_build_object('ordinal', policy.ordinal, 'name', policy.name, 'path', policy.compact->>'path',
            'category', {category}, 'category_known', {rank} < {len(CATEGORY_ORDER)}, 'sequence', {sequence},
            'scope', policy.data#>'{{configuration,scope}}', 'stateful', policy.data#>'{{configuration,stateful}}',
            'rule_count', policy.data->'rule_count', 'status', policy.data->>'status',
            'saved_rules', COALESCE(rules.total, 0), 'disabled_rules', COALESCE(rules.disabled, 0),
            'category_count', count(*) OVER (PARTITION BY {category}))
        FROM inventory_snapshotrecord policy
        LEFT JOIN rules ON rules.policy_path = policy.compact->>'path'
        WHERE policy.snapshot_id = %s AND policy.view = 'dfw'
          AND (policy.data #> '{{configuration,rule}}' IS NULL OR policy.data #> '{{configuration,rule}}' = 'null'::jsonb)
        ORDER BY {rank}, {category}, {sequence} NULLS LAST, policy.name, policy.ordinal
        LIMIT %s OFFSET %s"""
    with connection.cursor() as cursor:
        cursor.execute(sql, [snapshot_id, snapshot_id, PAGE_SIZE + 1, page * PAGE_SIZE])
        rows = [json.loads(value) if isinstance(value, str) else value for (value,) in cursor.fetchall()]
    items = rows[:PAGE_SIZE]
    paths = sorted({path for item in items for path in (explicit_entries(item.get('scope')) or [])})
    names = dict(SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='inventory', compact__path__in=paths)
                 .values_list('compact__path', 'name')) if paths else {}
    for item in items:
        scope = explicit_entries(item.pop('scope', None))
        # Missing scope is not recorded; an empty or ANY scope applies to the whole DFW.
        item['applied_to'] = {'recorded': scope is not None, 'any': scope == [],
                              'items': [{'path': path, 'name': names.get(path, path.rsplit('/', 1)[-1]), 'known': path in names}
                                        for path in (scope or [])]}
    return {'items': items, 'page': page, 'has_next': len(rows) > PAGE_SIZE, 'page_size': PAGE_SIZE}
