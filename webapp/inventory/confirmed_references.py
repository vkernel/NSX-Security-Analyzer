"""Firewall references through confirmed links and separately labelled saved IP matches."""
from django.db import connection
from .firewall_reference_context import enrich


def with_ip_context(snapshot_id,ordinal,evidence):
    from .ip_references import context
    groups,metadata=context(snapshot_id,ordinal)
    for path,edge in groups.items():
        previous=evidence.get(path,{})
        evidence[path]={**edge,**previous,'ip_matches':edge['ip_matches'],'ip_checked_at':edge['ip_checked_at']}
    metadata['has_saved_ip']=bool(groups)
    return evidence,metadata


def current_context(snapshot, owner):
    """Current direct associations plus timestamped saved attachment and IP evidence.

    Read only this VM's indexed evidence. Never infer from tags, expand arbitrary
    parent expressions, contact NSX or write historical links to the live cache.
    """
    from .current_membership import namespace, digest, GROUP
    from .network_membership import PORT
    from .models import MembershipRelation, SnapshotRecord
    evidence = {path:{'path':path,'membership_basis':'direct_vm','membership_source':'current_nsx'}
        for path in MembershipRelation.objects.filter(environment=snapshot.environment,
            namespace=namespace(snapshot.environment), vm_key=digest(owner['identity']), present=True
        ).values_list('group__path',flat=True) if isinstance(path,str) and GROUP.fullmatch(path)}
    record = SnapshotRecord.objects.filter(snapshot=snapshot,view='vms',ordinal=owner['ordinal']).values(
        'data__network_groups','compact__network_membership_version','compact__network_membership_status',
        'compact__network_membership_checked_at').first() or {}
    stamp = record.get('compact__network_membership_checked_at')
    network_status = record.get('compact__network_membership_status','not_collected')
    if record.get('compact__network_membership_version') == 1:
        edges = record.get('data__network_groups')
        if not isinstance(edges,list):
            edges, network_status = [], 'partial'
        for edge in edges:
            if not isinstance(edge,dict) or not isinstance(edge.get('path'),str) or not GROUP.fullmatch(edge['path']):
                network_status = 'partial'
                continue
            links = edge.get('via') if isinstance(edge.get('via'),list) else []
            via = [link for link in links if isinstance(link,dict)
                and isinstance(link.get('port_path'),str) and PORT.fullmatch(link['port_path'])
                and link.get('segment_path')==link['port_path'].rsplit('/ports/',1)[0]
                and (link.get('interface_id') or link.get('attachment_id'))]
            if not via:
                network_status = 'partial'
                continue
            direct = edge['path'] in evidence
            evidence[edge['path']] = dict(edge,via=via,
                membership_basis='direct_and_network' if direct else 'network_attachment',
                membership_source='current_and_saved' if direct else 'saved_snapshot',network_checked_at=stamp)
    network={'network_status':network_status,'network_checked_at':stamp,
        'has_saved_network':any(e.get('membership_source') in ('saved_snapshot','current_and_saved') for e in evidence.values())}
    evidence,ip_metadata=with_ip_context(snapshot.pk,owner['ordinal'],evidence)
    return evidence,{**network,**ip_metadata}


def read(snapshot_id, groups, page=0, path='', detail=False, evidence=None):
    if not groups:
        return None if detail else {'items':[], 'page':page, 'has_next':False}
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
    for item in items:
        bases = {(evidence or {}).get(group,{}).get('membership_basis') for group in item['via_groups']}
        confirmed = any(bases & {'direct_vm','network_attachment','direct_and_network'}) or evidence is None
        ip_matches=[dict(match,group_path=group) for group in item['via_groups']
                    for match in (evidence or {}).get(group,{}).get('ip_matches',[])]
        item['reference_reason'] = ('Verified VM interface attachment; saved parent-group port reference' if bases=={'network_attachment'}
            else 'Direct VM membership and verified interface attachment; saved group references' if bases & {'network_attachment','direct_and_network'}
            else 'Confirmed VM membership; saved group reference')
        saved = [(evidence or {}).get(group,{}) for group in item['via_groups']
                 if (evidence or {}).get(group,{}).get('membership_basis') in ('network_attachment','direct_and_network')
                 and (evidence or {}).get(group,{}).get('membership_source') in ('saved_snapshot','current_and_saved')]
        if saved:
            item['network_checked_at'] = saved[0].get('network_checked_at')
            item['reference_reason'] += '. Attachment evidence is saved; current refresh checks direct VM associations only.'
        if ip_matches:
            item.update(ip_matches=ip_matches[:12],ip_match_count=len(ip_matches),ip_matches_truncated=len(ip_matches)>12 or any(evidence[g].get('ip_matches_truncated') for g in item['via_groups']),ip_checked_at=next((evidence[g].get('ip_checked_at') for g in item['via_groups'] if evidence[g].get('ip_matches')),None),
                        reference_basis='confirmed_and_ip' if confirmed else 'ip_subnet')
            item['reference_reason'] = ((item['reference_reason']+'; also matches saved VM IP evidence.') if confirmed else
                'Saved IP/subnet configuration match; not confirmed VM membership or effective policy.')
        if item.get('exclusion_reference'):
            item['reference_reason'] += ' Excluded fields are negated references, not positive endpoint matches.'
    return {'items':items,'page':page,'has_next':len(rows)>25}


def saved(snapshot_id, ordinal, section, page=0, path=''):
    from .models import SnapshotRecord
    row = SnapshotRecord.objects.filter(snapshot_id=snapshot_id, view='vms', ordinal=ordinal).values('data__resolved_groups','compact__resolved_membership_status').get()
    groups = [group['path'] for group in row['data__resolved_groups'] or [] if isinstance(group,dict) and isinstance(group.get('path'),str)]
    evidence = {group['path']:{'membership_basis':'direct_vm',**group} for group in row['data__resolved_groups'] or [] if isinstance(group,dict) and isinstance(group.get('path'),str)}
    evidence,ip_metadata=with_ip_context(snapshot_id,ordinal,evidence)
    groups=list(evidence)
    if section == 'confirmed_rule':
        from django.http import Http404
        detail = read(snapshot_id,groups,path=path,detail=True)
        if detail is None: raise Http404('Rule is not referenced through this VM\'s saved membership or IP evidence.')
        detail['via_groups']=sorted(set(groups).intersection(detail.get('source_groups',[])+detail.get('destination_groups',[])+detail.get('scope',[])))
        detail['membership_evidence']={group:evidence[group] for group in detail['via_groups']}
        return {'details':detail}
    if section == 'confirmed_via_groups':
        detail = read(snapshot_id,groups,path=path,detail=True)
        from django.http import Http404
        if detail is None: raise Http404
        values = sorted(set(groups).intersection(detail.get('source_groups',[])+detail.get('destination_groups',[])+detail.get('scope',[])))
        return {'items':values[page*25:page*25+25],'has_next':len(values)>(page+1)*25,'page':page}
    result = read(snapshot_id, groups, page, evidence=evidence)
    result.update(status=row['compact__resolved_membership_status'] or 'not_collected',
        source='saved_snapshot', **ip_metadata,notes=['Saved VM groups, attachments and separately labelled IP-definition references; not an effective policy or traffic verdict.']+ip_metadata['ip_notes'][1:])
    if ip_metadata['ip_status'] not in ('complete','not_applicable'):result['status']='partial'
    return result
