"""Bounded reads of saved group members. Browsing never contacts NSX."""
import json
from django.db import connection
from django.http import Http404
from .models import SnapshotRecord
from .resolved_membership import ENDPOINTS


def read(snapshot_id, path, section='virtual-machines', page=0):
    if section not in ENDPOINTS + ('network-virtual-machines',) or page < 0 or page > 1000000:
        raise ValueError('Invalid member category or page')
    owner = SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='inventory',
        compact__path=path, data__kind='group').values_list('pk', flat=True).first()
    if owner is None:
        raise Http404('Group is not available in this snapshot')
    with connection.cursor() as cursor:
        cursor.execute("""WITH source AS (
            SELECT data->'resolved_membership' AS evidence,
                   data->'resolved_membership'->'endpoints'->%s AS endpoint
            FROM inventory_snapshotrecord WHERE id = %s)
            SELECT jsonb_build_object(
                'status', COALESCE(endpoint->'status', '"not_collected"'::jsonb),
                'checked_at', evidence->'checked_at', 'notes', COALESCE(endpoint->'notes', '[]'::jsonb),
                'reported_count', endpoint->'reported_count',
                'saved_count', jsonb_array_length(COALESCE(endpoint->'items', '[]'::jsonb)),
                'items', (SELECT COALESCE(jsonb_agg(item ORDER BY position), '[]'::jsonb)
                    FROM (SELECT item, position FROM jsonb_array_elements(
                        COALESCE(endpoint->'items', '[]'::jsonb)) WITH ORDINALITY AS e(item, position)
                        ORDER BY position LIMIT 26 OFFSET %s) page)) FROM source""", [section, owner, page*25])
        value = cursor.fetchone()[0]
    result = json.loads(value) if isinstance(value, str) else value
    result.update(page=page, has_next=len(result['items']) > 25)
    result['items'] = result['items'][:25]
    return result
