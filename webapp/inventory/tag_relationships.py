"""Bounded tag/scope evidence for existing indexes; never expand all references in Python."""
import json
from django.db import connection
from django.http import Http404
from .models import SnapshotRecord

PAGE_SIZE = 25
MAPS = {'vms', 'group_conditions', 'group_assignments', 'other_assignments'}
CONDITIONS = {'condition_evidence': 'condition_evidence_set', 'review_conditions': 'review_condition_set'}


def read(snapshot_id, ordinal, section='summary', page=0):
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
    if row['relationships_ready']:
        from .models import SnapshotRelationship
        start = page * PAGE_SIZE
        items = list(SnapshotRelationship.objects.filter(record_id=row['pk'], section=section, position__gte=start, position__lt=start + PAGE_SIZE + 1).order_by('position').values_list('data', flat=True))
        return {'items': items[:PAGE_SIZE], 'page': page, 'has_next': len(items) > PAGE_SIZE}
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
        else:
            sql = "WITH source AS (SELECT data->'firewall_references' AS items " + base + "), page AS (SELECT value, position FROM source, jsonb_array_elements(COALESCE(items, '[]'::jsonb)) WITH ORDINALITY AS e(value, position) ORDER BY position LIMIT 26 OFFSET %s) SELECT jsonb_build_object('name', rule->>'name', 'path', rule->>'path', 'rule_id', rule->'rule_id', 'disabled', rule->'disabled', 'via_group', value->'via_group', 'tag_use', value->'tag_use') FROM page LEFT JOIN inventory_snapshotpresentation p ON p.snapshot_id = %s LEFT JOIN LATERAL (SELECT p.tag_evidence->'firewall_rules'->(value->>'rule')::int AS rule) resolved ON true ORDER BY position"
            params = [*args, page * PAGE_SIZE, snapshot_id]
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        items = [json.loads(item[0]) if isinstance(item[0], str) else item[0] for item in cursor.fetchall()]
    return {'items': items[:PAGE_SIZE], 'page': page, 'has_next': len(items) > PAGE_SIZE}


def index_record(record, metadata):
    """Normalize the already prepared row without rendering or reloading its report."""
    from .models import SnapshotRelationship
    batch = []
    def add(section, position, data):
        batch.append(SnapshotRelationship(record=record, section=section, position=position, data=data))
        if len(batch) >= 250:
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
    if batch: SnapshotRelationship.objects.bulk_create(batch, batch_size=250)
