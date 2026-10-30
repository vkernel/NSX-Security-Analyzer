"""Bounded configuration relationships. Never a reachability or traffic evaluator."""
from collections import defaultdict
import math
from .naming import group_evidence, digest
from .models import SnapshotRecord

MAX_GROUPS = 12
MAX_RULES = 10000


def build(snapshot, query='', scope='', service='', include_disabled=False, hidden_paths=()):
    groups = {}
    for item in SnapshotRecord.objects.filter(snapshot=snapshot, data__kind='group').values('name', 'data__path', 'data__configuration', 'data__membership').iterator(chunk_size=500):
        path = item['data__path']
        if not path: continue
        evidence = group_evidence(item['data__configuration'])
        values = sorted({t['tag'] for t in evidence['tags'] if t['scope'] == scope}) if scope else []
        groups[path] = {'path': path, 'name': item['name'], 'type': evidence['group_type'],
                        'membership': item['data__membership'] or 'unknown', 'classification': ', '.join(values) or 'Unclassified',
                        'definition': item['data__configuration'], 'evidence': evidence}
    # Search VMs only when requested; these are potential configuration relationships.
    workloads = []
    if query:
        from django.db.models import Q
        for vm in SnapshotRecord.objects.filter(snapshot=snapshot, view='vms').filter(Q(name__icontains=query) | Q(search_basic__icontains=query) | Q(data__vm_details__icontains=query)).values('name','data__path','data__related_groups')[:51]:
            workloads.append({'name': vm['name'], 'path': vm['data__path'], 'groups': vm['data__related_groups'] or []})
    vm_paths = {g.get('path') for vm in workloads for g in vm['groups'] if isinstance(g, dict)}
    visible = [g for g in groups.values() if not query or query.casefold() in (g['name']+' '+g['path']+' '+g['classification']).casefold() or g['path'] in vm_paths]
    visible.sort(key=lambda g: (g['classification'], g['name']))
    hidden_groups=[g for g in visible if g['path'] in hidden_paths]
    visible=[g for g in visible if g['path'] not in hidden_paths]
    count = len(visible)
    visible = visible[:MAX_GROUPS]
    selected = {g['path'] for g in visible}
    cells, rules, warnings = defaultdict(list), [], []
    source = SnapshotRecord.objects.filter(snapshot=snapshot, data__configuration__rule__isnull=False).order_by('ordinal').values('data')[:MAX_RULES+1]
    source = list(source)
    service_paths={path for row in source[:MAX_RULES] for path in (row['data'].get('configuration',{}).get('rule',{}).get('services') or []) if path != 'ANY'}
    service_catalog={r['data__path']:{'name':r['name'],'definition':r['data__configuration']} for r in SnapshotRecord.objects.filter(snapshot=snapshot,data__kind='service',data__path__in=service_paths).values('name','data__path','data__configuration')}
    if len(source)>MAX_RULES: warnings.append('Rule limit reached. Relationships are partial; narrow the evidence in the firewall inventory.')
    for record in source[:MAX_RULES]:
        row = record['data']; config = row.get('configuration') or {}; rule = config.get('rule') or {}
        disabled = bool(rule.get('disabled', row.get('disabled')))
        if disabled and not include_disabled: continue
        services = rule.get('services', [])
        inline = rule.get('service_entries', [])
        service_evidence=[dict(path=p,**service_catalog.get(p,{'name':p,'definition':None})) for p in services]
        if service and service.casefold() not in str([service_evidence, inline]).casefold(): continue
        sources, destinations = rule.get('source_groups', []), rule.get('destination_groups', [])
        if not selected.intersection(sources+destinations) and 'ANY' not in sources+destinations: continue
        reasons = []
        if 'ANY' in sources: reasons.append('Broad ANY source')
        if 'ANY' in destinations: reasons.append('Broad ANY destination')
        if 'ANY' in services: reasons.append('Broad ANY service')
        excluded = bool(rule.get('sources_excluded') or rule.get('destinations_excluded'))
        if excluded: reasons.append('Negated endpoints require manual assessment')
        if not sources or not destinations: reasons.append('Endpoint definition unavailable')
        entry = dict(path=row.get('path',''), name=row.get('name','Unnamed rule'), policy=row.get('policy_name',''),
                     action=rule.get('action',row.get('action','Unknown')), disabled=disabled, sources=sources,
                     destinations=destinations, services=services, service_evidence=service_evidence, inline=inline, scope=rule.get('scope',config.get('policy',{}).get('scope',[])),
                     order=rule.get('sequence_number'), direction=rule.get('direction','Not recorded'), reasons=reasons,
                     configuration=config, hits=row.get('hit_count'), fingerprint=digest(config))
        rules.append(entry)
        # ANY is a symbolic endpoint, never materialized into every workload pair.
        if not excluded:
            for a in set(sources) & (selected | {'ANY'}):
                for b in set(destinations) & (selected | {'ANY'}): cells[a,b].append(entry)
    if any('ANY' in r['sources']+r['destinations'] for r in rules):
        visible.append({'path':'ANY','name':'ANY','type':'symbolic','membership':'Not a workload group','classification':'Any matching endpoint'})
    matrix, edges = [], []
    for a in visible:
        row=[]
        for b in visible:
            evidence=cells[a['path'],b['path']]
            actions={r['action'] for r in evidence if not r['disabled']}
            state='mixed' if len(actions)>1 else 'allow' if actions=={'ALLOW'} else 'drop' if actions and actions <= {'DROP','REJECT'} else 'unknown'
            label={'mixed':'Mixed rule actions','allow':'Allow rule found','drop':'Drop/reject rule found','unknown':'Not assessed'}[state]
            if evidence and not actions: label='Disabled rules only'
            cell={'source':a,'destination':b,'rules':evidence[:25],'rule_count':len(evidence),'rules_truncated':len(evidence)>25,'state':state,'label':label,'index':len(edges)}
            if evidence: edges.append(cell)
            row.append(cell)
        matrix.append({'source':a,'cells':row})
    graph_nodes=[]
    for i, group in enumerate(visible):
        angle=2*math.pi*i/max(1,len(visible))
        graph_nodes.append(dict(group,x=450+300*math.cos(angle),y=320+220*math.sin(angle)))
    positions={g['path']:g for g in graph_nodes}
    graph_edges=[]
    for edge in edges:
        a=positions[edge['source']['path']];b=positions[edge['destination']['path']]
        if a['path']==b['path']: continue
        dx=b['x']-a['x'];dy=b['y']-a['y'];length=max(1,math.hypot(dx,dy))
        graph_edges.append(dict(edge,x1=a['x']+30*dx/length,y1=a['y']+30*dy/length,x2=b['x']-35*dx/length,y2=b['y']-35*dy/length))
    return dict(hidden_groups=hidden_groups,graph_nodes=graph_nodes,graph_edges=graph_edges,groups=visible, group_total=count, matrix=matrix, edges=edges, rules=rules,
                priorities=[r for r in rules if r['reasons']], workloads=workloads[:50], workloads_truncated=len(workloads)>50,
                warnings=warnings, truncated=count>MAX_GROUPS, rule_limit=MAX_RULES)
