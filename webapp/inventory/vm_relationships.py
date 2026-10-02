"""Bounded VM evidence projections, including previously prepared snapshots."""
from django.db import connection
from django.http import Http404
from .models import SnapshotRecord


def read(snapshot_id, ordinal, section='summary', page=0, path=''):
    row = SnapshotRecord.objects.filter(snapshot_id=snapshot_id, ordinal=ordinal, view='vms')
    summary = row.values('compact').first()
    if summary is None:
        raise Http404
    if section == 'summary':
        return {'data': summary['compact']}
    page = max(0, page)
    # Values remain bound parameters. Only fixed JSON keys enter SQL text.
    base = 'FROM inventory_snapshotrecord WHERE snapshot_id = %s AND ordinal = %s AND view = %s'
    args = [snapshot_id, ordinal, 'vms']
    if section == 'vm':
        with connection.cursor() as cursor:
            cursor.execute("SELECT data->'vm_details' " + base, args)
            import json
            value = cursor.fetchone()[0]
            return {'details': json.loads(value) if isinstance(value, str) else value}
    if section not in ('tags', 'related_groups', 'related_rules', 'rule', 'via_groups'):
        raise ValueError('Unknown relationship section')
    key = 'related_rules' if section in ('rule', 'via_groups') else section
    cte = "WITH source AS (SELECT data->%s AS items " + base + "), entries AS (SELECT value AS item FROM source, jsonb_array_elements(COALESCE(items, '[]'::jsonb))) "
    with connection.cursor() as cursor:
        if section == 'rule':
            cursor.execute(cte + "SELECT item - 'via_group' - 'via_groups' FROM entries WHERE item->>'path' = %s LIMIT 1", [key, *args, path])
            found = cursor.fetchone()
            if not found:
                raise Http404
            # Details are for a single rule, not the full VM's expanded rule set.
            import json
            detail = json.loads(found[0]) if isinstance(found[0], str) else found[0]
            detail.pop('via_groups', None)
            detail.pop('via_group', None)
            return {'details': detail}
        if section == 'via_groups':
            cursor.execute(cte + "SELECT DISTINCT g.value FROM entries, jsonb_array_elements(COALESCE(item->'via_groups', jsonb_build_array(item->'via_group'))) AS g WHERE item->>'path' = %s ORDER BY g.value LIMIT 26 OFFSET %s", [key, *args, path, page*25])
            items = [value[0] for value in cursor.fetchall()]
        elif section == 'related_rules':
            # Deduplicate legacy per-group entries before paging; keep provenance on group pages.
            query = "SELECT jsonb_build_object('path', item->>'path', 'name', min(item->>'name')) FROM entries GROUP BY item->>'path' ORDER BY min(item->>'name'), item->>'path' LIMIT 26 OFFSET %s"
        else:
            query = 'SELECT item FROM entries LIMIT 26 OFFSET %s'
        if section != 'via_groups':
            cursor.execute(cte + query, [key, *args, page*25])
            items = [value[0] for value in cursor.fetchall()]
    # psycopg raw JSONB is text with Django's connection configuration.
    import json
    items = [json.loads(value) if isinstance(value, str) else value for value in items]
    return {'items': items[:25], 'page': page, 'has_next': len(items) > 25}
