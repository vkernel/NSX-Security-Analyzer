"""Bounded references for saved groups/services, shared with workload firewall tables."""
import json
from django.db import connection
from django.http import Http404
from .models import SnapshotRecord
from .firewall_reference_context import enrich


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
    with connection.cursor() as cursor:
        cursor.execute("""WITH source AS (
            SELECT data->'referenced_by' AS refs, data->'reference_details' AS details
            FROM inventory_snapshotrecord WHERE id = %s), paths AS (
            SELECT DISTINCT value AS path FROM source,
                jsonb_array_elements_text(COALESCE(refs, '[]'::jsonb))
            WHERE (position('/rules/' in value) > 0) = %s
            ORDER BY value LIMIT %s OFFSET %s)
            SELECT jsonb_build_object('path', paths.path, 'name',
                (SELECT item->>'name' FROM source,
                    jsonb_array_elements(COALESCE(details, '[]'::jsonb)) item
                    WHERE item->>'path' = paths.path LIMIT 1), 'via_group', %s)
            FROM paths ORDER BY paths.path""", [owner['pk'], section == 'firewall', size+1, page*size, path])
        rows = [json.loads(value) if isinstance(value, str) else value for (value,) in cursor.fetchall()]
    items = rows[:size]
    if section == 'firewall':
        items = enrich(snapshot_id, items, object_kind=kind,full=full)
    return {'items': items, 'page': page, 'has_next': len(rows) > size}
