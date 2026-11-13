"""Bounded tag/scope evidence for existing indexes; never expand all references in Python."""
import json
from django.db import connection
from django.db.models.functions import Coalesce
from django.http import Http404
from .models import SnapshotRecord

PAGE_SIZE = 25
MAPS = {'vms', 'group_conditions', 'group_assignments', 'other_assignments'}
CONDITIONS = {'condition_evidence': 'condition_evidence_set', 'review_conditions': 'review_condition_set'}


def read(snapshot_id, ordinal, section='summary', page=0, context=False, *, full=False):
    if page < 0 or page > 1000000:
        raise ValueError('Invalid evidence page.')
    row = SnapshotRecord.objects.filter(snapshot_id=snapshot_id, ordinal=ordinal, view__in=['tags', 'scopes']).values('pk', 'view', 'compact', 'relationships_ready').first()
    if row is None:
        raise Http404('Tag or scope is not available in this snapshot.')
    if section == 'summary':
        return {'data': row['compact'], 'view': row['view']}
    allowed = {'tags'} if row['view'] == 'scopes' else MAPS | set(CONDITIONS) | {'firewall_references', 'notes'}
    if section not in allowed:
        raise ValueError('Unknown relationship section.')
    if section == 'firewall_references':
        return page_result(snapshot_id, firewall_page(snapshot_id, row, page), page, section, context, full=full)
    if row['relationships_ready']:
        from .models import SnapshotRelationship
        start = page * PAGE_SIZE
        items = list(SnapshotRelationship.objects.filter(record_id=row['pk'], section=section, position__gte=start, position__lt=start + PAGE_SIZE + 1).order_by('position').annotate(resolved=Coalesce('payload__data', 'data')).values_list('resolved', flat=True))
        return page_result(snapshot_id, items, page, section, context,full=full)
    base = 'FROM inventory_snapshotrecord r WHERE r.snapshot_id = %s AND r.ordinal = %s'
    args = [snapshot_id, ordinal]
    if section in MAPS:
        sql = "WITH source AS (SELECT data->%s AS items " + base + ") SELECT jsonb_build_object('path', key, 'name', CASE WHEN jsonb_typeof(value) = 'object' THEN value->>'name' ELSE value #>> '{}' END, 'resource_type', value->>'resource_type') FROM source, jsonb_each(COALESCE(items, '{}'::jsonb)) ORDER BY key LIMIT 26 OFFSET %s"
        params = [section, *args, page * PAGE_SIZE]
    elif section in ('tags', 'notes'):
        sql = "WITH source AS (SELECT data->%s AS items " + base + ") SELECT value FROM source, jsonb_array_elements(COALESCE(items, '[]'::jsonb)) WITH ORDINALITY AS e(value, position) ORDER BY position LIMIT 26 OFFSET %s"
        params = [section, *args, page * PAGE_SIZE]
    else:
        # Slice IDs/edges in PostgreSQL before resolving shared definitions. A large
        # tag must not copy the entire global rule/condition metadata to the client.
        source = 'FROM inventory_snapshotrecord r LEFT JOIN inventory_snapshotpresentation p ON p.snapshot_id = r.snapshot_id WHERE r.snapshot_id = %s AND r.ordinal = %s'
        if section in CONDITIONS:
            sql = "WITH source AS (SELECT CASE WHEN r.data ? %s THEN p.tag_evidence->'condition_sets'->(r.data->>%s)::int ELSE r.data->%s END AS items " + source + "), page AS (SELECT value, position FROM source, jsonb_array_elements(COALESCE(items, '[]'::jsonb)) WITH ORDINALITY AS e(value, position) ORDER BY position LIMIT 26 OFFSET %s) SELECT CASE WHEN jsonb_typeof(value) = 'number' THEN p.tag_evidence->'conditions'->(value::text)::int ELSE value END FROM page LEFT JOIN inventory_snapshotpresentation p ON p.snapshot_id = %s ORDER BY position"
            params = [CONDITIONS[section], CONDITIONS[section], section, *args, page * PAGE_SIZE, snapshot_id]

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        items = [json.loads(item[0]) if isinstance(item[0], str) else item[0] for item in cursor.fetchall()]
    return page_result(snapshot_id, items, page, section, context,full=full)


def firewall_page(snapshot_id, row, page):
    """Tag firewall edges in configured NSX order; sorting precedes paging."""
    from .firewall_reference_context import nsx_order_sql
    if row['relationships_ready']:
        refs = """SELECT COALESCE(j.data, s.data) AS item, s.position FROM inventory_snapshotrelationship s
            LEFT JOIN inventory_sharedsnapshotjson j ON j.id = s.payload_id
            WHERE s.record_id = %s AND s.section = 'firewall_references'"""
        params = [row['pk']]
    else:
        # Legacy rows keep rule metadata in the snapshot presentation.
        refs = """SELECT jsonb_build_object('name', rule->>'name', 'path', rule->>'path', 'rule_id', rule->'rule_id',
                'disabled', rule->'disabled', 'via_group', value->'via_group', 'tag_use', value->'tag_use') AS item, position
            FROM inventory_snapshotrecord r LEFT JOIN inventory_snapshotpresentation p ON p.snapshot_id = r.snapshot_id,
                jsonb_array_elements(COALESCE(r.data->'firewall_references', '[]'::jsonb)) WITH ORDINALITY AS e(value, position)
                LEFT JOIN LATERAL (SELECT p.tag_evidence->'firewall_rules'->(value->>'rule')::int AS rule) resolved ON true
            WHERE r.id = %s"""
        params = [row['pk']]
    # One row per rule: a rule reached through several tag-using groups lists all of them.
    order_select, order = nsx_order_sql('saved_rule.data', 'rules.position')
    sql = f"""WITH refs AS ({refs}), rules AS (
            SELECT (array_agg(item ORDER BY position))[1] - 'via_group' - 'tag_use' AS item, min(position) AS position,
                COALESCE(jsonb_agg(DISTINCT item->'via_group') FILTER (WHERE jsonb_typeof(item->'via_group') = 'string'), '[]'::jsonb) AS via_groups,
                COALESCE(jsonb_agg(DISTINCT item->'tag_use') FILTER (WHERE jsonb_typeof(item->'tag_use') = 'string'), '[]'::jsonb) AS tag_uses
            FROM refs GROUP BY COALESCE(item->>'path', 'position:' || position))
        SELECT rules.item || jsonb_build_object('via_groups', rules.via_groups, 'tag_uses', rules.tag_uses, 'nsx_order', {order_select})
        FROM rules LEFT JOIN inventory_snapshotrecord saved_rule ON saved_rule.snapshot_id = %s AND saved_rule.view = 'dfw'
            AND saved_rule.compact->>'path' = rules.item->>'path'
        ORDER BY {order} LIMIT %s OFFSET %s"""
    with connection.cursor() as cursor:
        cursor.execute(sql, [*params, snapshot_id, PAGE_SIZE + 1, page * PAGE_SIZE])
        return [json.loads(item) if isinstance(item, str) else item for (item,) in cursor.fetchall()]


def page_result(snapshot_id, items, page, section, context, *, full=False):
    page_items = items[:PAGE_SIZE]
    if context and section == 'firewall_references':
        from .firewall_reference_context import enrich
        page_items = enrich(snapshot_id, page_items,full=full)
    return {'items': page_items, 'page': page, 'has_next': len(items) > PAGE_SIZE}


def index_record(record, metadata, batch=None):
    """Normalize the already prepared row without rendering or reloading its report."""
    from .models import SnapshotRelationship
    own_batch = batch is None
    if own_batch:
        batch = []
    def add(section, position, data):
        batch.append(SnapshotRelationship(record=record, section=section, position=position, data=data))
        if len(batch) >= 250:
            from .storage import share_json
            share_json(batch)
            SnapshotRelationship.objects.bulk_create(batch, batch_size=250)
            batch.clear()
    data = record.data
    if record.view == 'scopes':
        for i, item in enumerate(data.get('tags', [])): add('tags', i, item)
    else:
        for section in sorted(MAPS):
            for i, (path, value) in enumerate(sorted(data.get(section, {}).items())):
                add(section, i, {'path': path, 'name': value.get('name') if isinstance(value, dict) else value,
                                 'resource_type': value.get('resource_type') if isinstance(value, dict) else None})
        for i, note in enumerate(data.get('notes', [])): add('notes', i, note)
        for section, shared in CONDITIONS.items():
            items = metadata.get('condition_sets', [])[data[shared]] if shared in data else data.get(section, [])
            for i, item in enumerate(items):
                add(section, i, metadata['conditions'][item] if isinstance(item, int) else item)
        for i, edge in enumerate(data.get('firewall_references', [])):
            rule = metadata['firewall_rules'][edge['rule']]
            add('firewall_references', i, {**{k: rule.get(k) for k in ('name', 'path', 'rule_id', 'disabled')},
                                          **{k: edge.get(k) for k in ('via_group', 'tag_use')}})
    if own_batch and batch:
        from .storage import share_json
        share_json(batch)
        SnapshotRelationship.objects.bulk_create(batch, batch_size=250)
