"""Explain candidate configuration rules for a VM pair; never an access verdict."""
import ipaddress
from .models import SnapshotRecord
from .naming import group_evidence

RULE_LIMIT = 10000
CATEGORIES = {'Ethernet': 0, 'Emergency': 1, 'Infrastructure': 2, 'Environment': 3, 'Application': 4}


def addresses(value):
    """Only address-bearing fields, not arbitrary strings such as gateways or IDs."""
    found = set()
    def parse(raw):
        if isinstance(raw, list):
            for child in raw: parse(child)
        elif isinstance(raw, str):
            try: found.add(str(ipaddress.ip_interface(raw).ip))
            except ValueError: pass
    def walk(obj):
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key in ('ip_address', 'ip_addresses', 'ipv4_addresses', 'ipv6_addresses'): parse(val)
                elif isinstance(val, (dict, list)): walk(val)
        elif isinstance(obj, list):
            for child in obj: walk(child)
    walk(value)
    return sorted(found)


def vm_summary(record):
    data = record.data
    return {'id': record.pk, 'path': data.get('path', ''), 'name': record.name,
            'ips': addresses(data.get('vm_details', {})), 'tags': data.get('tags') or [],
            'details': data.get('vm_details') or {}, 'related_groups': data.get('related_groups') or []}


def contains(network, ips):
    try:
        net = ipaddress.ip_network(network, strict=False)
        return any(ipaddress.ip_address(ip) in net for ip in ips)
    except ValueError:
        return False


def memberships(vm, groups):
    candidates = {r.get('path'): 'Potential membership from collected tag relationships' for r in vm['related_groups'] if isinstance(r, dict)}
    identities = {vm['path'], vm['details'].get('external_id'), vm['details'].get('id')}
    tags = {(t.get('scope',''),t.get('tag')) for t in vm['tags'] if isinstance(t,dict)}
    for path, group in groups.items():
        ev = group['evidence']
        for member in ev['members']:
            if member['kind'] == 'ExternalIDExpression' and member['member_type'] == 'VirtualMachine' and member['value'] in identities:
                candidates[path] = 'Explicit VM identity in saved group definition'
            elif member['kind'] == 'PathExpression' and member['value'] in identities:
                candidates[path] = 'Explicit VM path in saved group definition'
            elif member['kind'] == 'IPAddressExpression' and contains(member['value'],vm['ips']):
                candidates[path] = 'Saved VM address matches group IP definition'
        if any((t['scope'],t['tag']) in tags for t in ev['candidate_tags']):
            candidates.setdefault(path, 'Potential membership from tag conditions; full expression not evaluated')
    # Bounded closure, including cycles; does not upgrade inferred membership to verified.
    for _ in range(30):
        added=False
        for path, group in groups.items():
            if path not in candidates and any(child in candidates for child in group['evidence']['nested']):
                candidates[path]='Potential nested membership; full expression not evaluated';added=True
        if not added: break
    return [{'path':path, 'name':groups.get(path,{}).get('name',path), 'type':groups.get(path,{}).get('evidence',{}).get('group_type','unknown'),
             'reason':reason, 'status':'Configuration evidence'} for path,reason in sorted(candidates.items()) if path]


def endpoint(values, vm, members, excluded=False):
    if excluded: return 'unknown', ['Negated endpoint requires manual assessment']
    if not values: return 'unknown', ['Endpoint definition unavailable']
    names={g['path']:g['name'] for g in members}
    matches=[];unknown=[]
    for value in values:
        if value == 'ANY': matches.append('ANY (any matching endpoint, not necessarily internet)')
        elif value in names: matches.append(names[value])
        elif contains(value,vm['ips']): matches.append(value+' (saved VM address)')
        elif value.startswith('/'):
            unknown.append(value)
        else:
            try: ipaddress.ip_network(value,strict=False)
            except ValueError: unknown.append(value)
            if not vm['ips']: unknown.append(value)
    if matches: return 'candidate', matches
    if unknown: return 'unknown', ['Membership/address evidence unresolved for '+v for v in unknown[:5]]
    return 'no_match', ['No match in saved VM address evidence']


def service_match(rule, catalog, protocol='', port=''):
    """Return unknown for unsupported services, recursion and missing definitions."""
    if not protocol and not port: return 'candidate', 'All services (no service filter)'
    entries=list(rule.get('service_entries') or [])
    unresolved=False;seen=set()
    def expand(path, depth=0):
        nonlocal unresolved
        if depth>12 or path in seen or len(seen)>100:
            unresolved=True;return
        seen.add(path)
        config=catalog.get(path)
        if not isinstance(config,dict): unresolved=True;return
        for entry in config.get('service_entries',[]):
            if entry.get('resource_type')=='NestedServiceServiceEntry': expand(entry.get('nested_service_path',''),depth+1)
            else: entries.append(entry)
    paths=rule.get('services') or []
    if 'ANY' in paths: return 'candidate', 'ANY service; inspect inline constraints and policy scope'
    for path in paths: expand(path)
    if not entries: return 'unknown', 'Service definition unavailable'
    for entry in entries:
        kind=entry.get('resource_type')
        if kind=='L4PortSetServiceEntry':
            p=entry.get('l4_protocol','').upper()
            if p not in ('TCP','UDP'): unresolved=True;continue
            if protocol and p!=protocol: continue
            if not port: return 'candidate', p+' service'
            ranges=entry.get('destination_ports') or []
            if not ranges: return 'candidate',p+' any destination port'
            for value in ranges:
                try:
                    parts=str(value).split('-');a=int(parts[0]);b=int(parts[-1])
                    if len(parts)>2 or not 0<=a<=b<=65535: raise ValueError()
                    if a<=int(port)<=b: return 'candidate',p+' destination port '+port
                except (ValueError,TypeError): unresolved=True
        elif kind=='ICMPTypeServiceEntry':
            if not port and protocol in ('','ICMP'): return 'candidate','ICMP (type/code require review)'
            if protocol not in ('TCP','UDP','ICMP'): unresolved=True
        else: unresolved=True
    return ('unknown','Unsupported or unresolved service definition') if unresolved else ('no_match','No matching protocol/destination port in saved service definition')


def investigate(snapshot, source_record, destination_record, protocol='', port='', include_disabled=False):
    source,destination=vm_summary(source_record),vm_summary(destination_record)
    groups={}
    for row in SnapshotRecord.objects.filter(snapshot=snapshot,data__kind='group').values('name','data__path','data__configuration'):
        groups[row['data__path']]={'name':row['name'],'evidence':group_evidence(row['data__configuration'])}
    source['groups']=memberships(source,groups);destination['groups']=memberships(destination,groups)
    catalog={r['data__path']:r['data__configuration'] for r in SnapshotRecord.objects.filter(snapshot=snapshot,data__kind='service').values('data__path','data__configuration')}
    rows=list(SnapshotRecord.objects.filter(snapshot=snapshot,data__configuration__rule__isnull=False).order_by('ordinal').values('data')[:RULE_LIMIT+1])
    candidates=[];unresolved=[];disabled_count=0
    for stored in rows[:RULE_LIMIT]:
        item=stored['data'];config=item.get('configuration') or {};rule=config.get('rule') or {};policy=config.get('policy') or {}
        disabled=bool(rule.get('disabled',item.get('disabled')))
        if disabled and not include_disabled: disabled_count+=1;continue
        a,why_a=endpoint(rule.get('source_groups'),source,source['groups'],rule.get('sources_excluded',False))
        b,why_b=endpoint(rule.get('destination_groups'),destination,destination['groups'],rule.get('destinations_excluded',False))
        svc,why_s=service_match(rule,catalog,protocol,port)
        if 'no_match' in (a,b,svc): continue
        scope=rule.get('scope',policy.get('scope',[]))
        sa,_=endpoint(scope,source,source['groups']);sb,_=endpoint(scope,destination,destination['groups'])
        entry={'name':item.get('name','Unnamed rule'),'path':item.get('path',''),'policy':item.get('policy_name',''),
            'category':item.get('category',policy.get('category','Unknown')), 'policy_order':policy.get('sequence_number'),
            'order':rule.get('sequence_number'), 'action':rule.get('action',item.get('action','Unknown')), 'disabled':disabled,
            'direction':rule.get('direction','Not recorded'), 'scope':scope, 'scope_status':'Potential endpoint scope match' if 'candidate' in (sa,sb) else 'Scope requires manual assessment',
            'source_reason':why_a,'destination_reason':why_b,'service_reason':why_s,
            'configuration':config,'services':rule.get('services',[]), 'inline':rule.get('service_entries',[]),
            'status':'candidate' if a==b==svc=='candidate' else 'unresolved'}
        (candidates if entry['status']=='candidate' else unresolved).append(entry)
    def order(row):
        def numeric(v): return v if isinstance(v,(int,float)) else 10**12
        return (CATEGORIES.get(row['category'],99),numeric(row['policy_order']),numeric(row['order']),row['path'])
    candidates.sort(key=order);unresolved.sort(key=order)
    return {'source':source,'destination':destination,'candidates':candidates,'unresolved':unresolved,
            'truncated':len(rows)>RULE_LIMIT,'disabled_count':disabled_count,'snapshot':snapshot,
            'membership_status':'Collected configuration / potential membership — not live verified'}
