"""Firewall configuration references through confirmed memberships, not tag candidates."""
from django.db import connection
from .firewall_reference_context import enrich


def read(snapshot_id, groups, page=0, path='', detail=False):
    if not groups:
        return {'items':[], 'page':page, 'has_next':False}
    # JSON array overlap stays in PostgreSQL; only one bounded rule page is read.
    with connection.cursor() as cursor:
        cursor.execute("""WITH candidates AS (
            SELECT data, COALESCE(data->'source_groups','[]'::jsonb) ||
                COALESCE(data->'destination_groups','[]'::jsonb) || COALESCE(data->'scope','[]'::jsonb) AS refs
            FROM inventory_snapshotrecord WHERE snapshot_id=%s AND view='dfw')
            SELECT data, (SELECT jsonb_agg(DISTINCT value) FROM jsonb_array_elements_text(refs)
                WHERE value=ANY(%s)) AS groups
            FROM candidates WHERE refs ?| %s AND (%s='' OR data->>'path'=%s)
            ORDER BY data->>'name', data->>'path' LIMIT 26 OFFSET %s""",
            [snapshot_id, groups, groups, path, path, page*25])
        import json
        rows = [(json.loads(row) if isinstance(row,str) else row, json.loads(via) if isinstance(via,str) else via) for row,via in cursor.fetchall()]
    if detail:
        return rows[0][0] if rows else None
    items = [{**{key:row[key] for key in ('name','path','action','disabled','policy_name') if key in row}, 'via_groups':via} for row,via in rows[:25]]
    items = enrich(snapshot_id, items, vm=True)
    for item in items: item['reference_reason']='Confirmed VM membership; saved group reference'
    return {'items':items,'page':page,'has_next':len(rows)>25}


def saved(snapshot_id, ordinal, section, page=0, path=''):
    from .models import SnapshotRecord
    row = SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='vms', ordinal=ordinal).values('data__resolved_groups','compact__resolved_membership_status').get()
    groups = [group['path'] for group in row['data__resolved_groups'] or [] if isinstance(group,dict) and isinstance(group.get('path'),str)]
    if section == 'confirmed_rule':
        from django.http import Http404
        detail = read(snapshot_id,groups,path=path,detail=True)
        if detail is None: raise Http404('Rule is not referenced through this VM\'s saved memberships.')
        return {'details':detail}
    if section == 'confirmed_via_groups':
        detail = read(snapshot_id,groups,path=path,detail=True)
        from django.http import Http404
        if detail is None: raise Http404
        values = sorted(set(groups).intersection(detail.get('source_groups',[])+detail.get('destination_groups',[])+detail.get('scope',[])))
        return {'items':values[page*25:page*25+25],'has_next':len(values)>(page+1)*25,'page':page}
    result = read(snapshot_id, groups, page)
    result.update(status=row['compact__resolved_membership_status'] or 'not_collected',
        source='saved_snapshot', notes=['Saved firewall configuration references through confirmed VM memberships. Incomplete membership coverage can omit rules; references do not establish effective policy.'])
    return result
