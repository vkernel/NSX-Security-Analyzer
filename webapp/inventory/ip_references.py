"""Saved IP-definition references, deliberately separate from VM membership.

Build the numeric range/VM-address index once per immutable snapshot, on demand.
Browsing queries matching ranges, never NSX or a VM-by-group collection scan.
"""
import ipaddress
import hashlib
from collections import Counter
from decimal import Decimal
from uuid import uuid4
from django.db import transaction
from django.db.models import Q,Count,F
from django.http import Http404
from .models import Snapshot,SnapshotRecord,SnapshotIPReferenceIndex,SnapshotIPReferenceRange,SnapshotVMAddress
from .current_membership import GROUP

VERSION=2
MAX_ADDRESSES=32
MAX_RANGES=100000
MAX_MATCHES=2000
MAX_EVIDENCE=32
SCOPE_KEYS=('guest_info','nics','interfaces','network_interfaces','network_interfaces_info')
ADDRESS_KEYS=('ip_address','ip_addresses','ipv4_addresses','ipv6_addresses','ipv4_address','ipv6_address')
IGNORE_KEYS=('gateway','gateways','default_gateway','dns','dns_servers','dhcp_server')


def scope(path):
    from .network_membership import PREFIX
    import re
    match=re.match('^('+PREFIX+')/(?:realized-state/virtual-machines|domains)/',path or '')
    return match.group(1) if match else '/infra'


def scope_key(path):
    # Fixed-size index keys also support long org/project identifiers.
    return hashlib.sha256(scope(path).encode()).hexdigest()


def addresses(details):
    """Use address fields in saved VM runtime data, never names/tags/gateways."""
    found=set();issues=Counter()
    def parse(value):
        if isinstance(value,list):
            for child in value:parse(child)
        elif isinstance(value,dict):
            for key in ADDRESS_KEYS:
                if key in value:parse(value[key])
        elif isinstance(value,str):
            try:
                if '%' in value:raise ValueError()
                ip=ipaddress.ip_interface(value.strip()).ip
                if ip.is_unspecified or ip.is_multicast or ip.is_loopback or ip.is_link_local or getattr(ip,'ipv4_mapped',None):
                    issues['nonportable']+=1;return
                found.add(str(ip))
            except ValueError:issues['invalid']+=1
    def walk(value):
        if isinstance(value,list):
            for child in value:walk(child)
        elif isinstance(value,dict):
            for key,child in value.items():
                if key in ADDRESS_KEYS:parse(child)
                elif key not in IGNORE_KEYS and not any(word in key.lower() for word in ('gateway','dns','dhcp','route')) and key not in ('tags','metadata') and isinstance(child,(dict,list)):walk(child)
    if isinstance(details,dict):
        for key in ADDRESS_KEYS:
            if key in details:parse(details[key])
        for key in SCOPE_KEYS:
            if key in details:walk(details[key])
    result=sorted(found,key=lambda value:(ipaddress.ip_address(value).version,int(ipaddress.ip_address(value))))
    if len(result)>MAX_ADDRESSES:issues['limit']+=1
    return result[:MAX_ADDRESSES],dict(issues)


def interval(value):
    if not isinstance(value,str) or '%' in value:raise ValueError('Invalid IP entry')
    value=value.strip()
    if '-' in value:
        left,right=value.split('-')
        lo,hi=ipaddress.ip_address(left.strip()),ipaddress.ip_address(right.strip())
        if lo.version!=hi.version or int(lo)>int(hi):raise ValueError('Invalid IP range')
        return lo.version,int(lo),int(hi),str(lo)+'-'+str(hi)
    network=ipaddress.ip_network(value,strict=False)
    label=str(network.network_address) if '/' not in value else str(network)
    return network.version,int(network.network_address),int(network.broadcast_address),label


def compile_groups(groups):
    """Only one explicit IP expression or a pure nested group union is supported.

    Any mixed/extended expression, missing child, cycle or limit stays unresolved;
    no child IP match can bypass additional AND/OR/identity conditions.
    """
    memo={};presence={};skipped=Counter();size=0
    def might_contain_ip(path,seen):
        if path in seen or len(seen)>=32:return True
        if path in presence:return presence[path]
        config=groups.get(path,{}).get('configuration')
        if not isinstance(config,dict):return True
        pending=[config]
        while pending:
            node=pending.pop()
            if isinstance(node,list):pending.extend(node)
            elif isinstance(node,dict):
                if node.get('resource_type')=='IPAddressExpression':presence[path]=True;return True
                if node.get('resource_type')=='PathExpression':
                    if not isinstance(node.get('paths'),list):return True
                    for child in node.get('paths',[]) if isinstance(node.get('paths'),list) else []:
                        if isinstance(child,str) and GROUP.fullmatch(child):
                            if child not in groups or might_contain_ip(child,seen|{path}):presence[path]=True;return True
                pending.extend(v for k,v in node.items() if k!='paths' and isinstance(v,(dict,list)))
        presence[path]=False
        return False
    def compile(path,seen):
        nonlocal size
        if path in seen or len(seen)>=32:return None
        if path in memo:return memo[path]
        config=groups[path]['configuration']
        result=None
        if isinstance(config,dict) and not config.get('extended_expression'):
            nodes=config.get('expression')
            if isinstance(nodes,list) and len(nodes)==1 and isinstance(nodes[0],dict):
                node=nodes[0]
                if node.get('resource_type')=='IPAddressExpression':
                    values=node.get('ip_addresses')
                    if isinstance(values,list) and len(values)<=25000:
                        try:
                            result=[(*interval(value),()) for value in values]
                            if len({row[0] for row in result})>1:result=None
                        except (ValueError,TypeError):result=None
                elif node.get('resource_type')=='PathExpression':
                    paths=node.get('paths')
                    if isinstance(paths,list) and paths and len(paths)<=500:
                        result=[]
                        for child in paths:
                            if not isinstance(child,str) or not GROUP.fullmatch(child) or child not in groups or scope(child)!=scope(path):
                                result=None;break
                            entries=compile(child,seen|{path})
                            if entries is None or len(result)+len(entries)>25000:result=None;break
                            result.extend((*entry[:4],(child,)+entry[4]) for entry in entries)
        if result is not None:
            if size+len(result)>MAX_RANGES:result=None
            else:size+=len(result)
        memo[path]=result
        return result
    for path,group in groups.items():
        entries=compile(path,set())
        if entries is None:
            if might_contain_ip(path,set()):skipped['unresolved_groups']+=1
        yield path,group,entries
    yield None,dict(skipped),None


def prepare(snapshot):
    if snapshot.browsing_status!='ready':raise Http404('Restore saved browsing evidence first.')
    warm=SnapshotIPReferenceIndex.objects.filter(snapshot=snapshot,version=VERSION).first()
    if warm is not None:return warm
    with transaction.atomic():
        projection,_=SnapshotIPReferenceIndex.objects.get_or_create(snapshot=snapshot)
        projection=SnapshotIPReferenceIndex.objects.select_for_update().get(pk=projection.pk)
        if projection.version==VERSION:return projection
        projection.snapshotipreferencerange_set.all().delete()
        projection.snapshotvmaddress_set.all().delete()
        groups={};conflicts=set()
        for row in SnapshotRecord.objects.filter(snapshot=snapshot,view='inventory',data__kind='group').order_by('ordinal','pk').values(
                'name','data__path','data__configuration').iterator(chunk_size=500):
            path=row['data__path']
            if not isinstance(path,str) or not GROUP.fullmatch(path):continue
            group={'name':row['name'],'configuration':row['data__configuration']}
            if path in groups and groups[path]!=group:conflicts.add(path)
            else:groups[path]=group
        for path in conflicts:groups[path]['configuration']=None
        metadata={'generation':uuid4().hex,'ranges':0,'unresolved_groups':0,'vm_issues':{}}
        pending=[]
        for path,group,entries in compile_groups(groups):
            if path is None:metadata.update(group);break
            if entries is None:continue
            entries=sorted(set(entries))
            if metadata['ranges']+len(entries)>MAX_RANGES:
                metadata['range_limit']=True;continue
            for family,lo,hi,entry,via in entries:
                pending.append(SnapshotIPReferenceRange(projection=projection,scope=scope_key(path),family=family,
                    lower=Decimal(lo),upper=Decimal(hi),group_path=path,group_name=group['name'],entry=entry,via=list(via)))
                metadata['ranges']+=1
                if len(pending)>=500:
                    SnapshotIPReferenceRange.objects.bulk_create(pending,batch_size=500);pending=[]
        SnapshotIPReferenceRange.objects.bulk_create(pending,batch_size=500)
        pending=[]
        for row in SnapshotRecord.objects.filter(snapshot=snapshot,view='vms').order_by('ordinal','pk').values('pk','compact__path','data__vm_details').iterator(chunk_size=500):
            ips,issues=addresses(row['data__vm_details'])
            if issues:metadata['vm_issues'][str(row['pk'])]=issues
            for value in ips:
                ip=ipaddress.ip_address(value)
                pending.append(SnapshotVMAddress(projection=projection,record_id=row['pk'],scope=scope_key(row['compact__path']),
                    family=ip.version,address=Decimal(int(ip)),ip=value))
            if len(pending)>=500:
                SnapshotVMAddress.objects.bulk_create(pending,batch_size=500);pending=[]
        SnapshotVMAddress.objects.bulk_create(pending,batch_size=500)
        projection.version,projection.metadata=VERSION,metadata
        projection.save(update_fields=['version','metadata'])
        return projection


def context(snapshot_id,ordinal):
    snapshot=Snapshot.objects.only('pk','generated_at','browsing_status','testing').annotate(inventory_warning=F('summary__inventory_warnings')).get(pk=snapshot_id)
    vm=SnapshotRecord.objects.only('pk').get(snapshot=snapshot,view='vms',ordinal=ordinal)
    projection=prepare(snapshot)
    observations=list(SnapshotVMAddress.objects.filter(projection=projection,record=vm).values('ip','family','address','scope'))
    stamp=snapshot.generated_at.isoformat()
    notes=['Saved IP-definition matches only; not confirmed VM membership, live addresses, effective policy or observed traffic.']
    result={'ip_status':'complete','ip_checked_at':stamp,'ip_addresses':[r['ip'] for r in observations],'ip_notes':notes}
    def finish(groups):
        if snapshot.testing or snapshot.inventory_warning:
            result['ip_status']='partial';notes.append('Collection coverage is limited or provisional; additional IP references may be missing.')
        if not SnapshotIPReferenceIndex.objects.filter(pk=projection.pk,version=VERSION,metadata__generation=projection.metadata['generation']).exists():
            result['ip_status']='partial';notes.append('Saved browsing evidence changed during this read; reopen to refresh the IP references.')
        return groups,result
    if not projection.metadata.get('ranges') and not projection.metadata.get('unresolved_groups') and not projection.metadata.get('range_limit'):
        result['ip_status']='not_applicable';notes.append('No supported explicit IP group definitions were saved.')
        return finish({})
    if not observations:
        result.update(ip_status='not_collected');notes.append('No usable VM addresses were saved. A current direct-membership refresh does not collect IP evidence.')
        return finish({})
    query=Q(pk__in=[])
    for row in observations:query|=Q(scope=row['scope'],family=row['family'],address=row['address'])
    shared={(r['scope'],r['family'],r['address']) for r in SnapshotVMAddress.objects.filter(query,projection=projection).values(
        'scope','family','address').annotate(owners=Count('record_id',distinct=True)).filter(owners__gt=1)}
    unique=[r for r in observations if (r['scope'],r['family'],r['address']) not in shared]
    if shared:notes.append('Shared VM addresses were excluded because their ownership is ambiguous within the saved scope.')
    meta=projection.metadata
    if meta.get('unresolved_groups'):notes.append(str(meta['unresolved_groups'])+' mixed, nested, missing or malformed group definitions could not be evaluated as IP-only expressions.')
    if meta.get('range_limit'):notes.append('Saved IP definition index limit reached; additional matches remain unknown.')
    issues=meta.get('vm_issues',{}).get(str(vm.pk))
    if issues:notes.append('Some invalid, scoped, nonportable or excess VM addresses were omitted.')
    if shared or issues or meta.get('unresolved_groups') or meta.get('range_limit'):result['ip_status']='partial'
    if not unique:return finish({})
    query=Q(pk__in=[])
    for row in unique:query|=Q(scope=row['scope'],family=row['family'],lower__lte=row['address'],upper__gte=row['address'])
    ranges=list(SnapshotIPReferenceRange.objects.filter(query,projection=projection).order_by('group_path','entry','pk').values(
        'group_path','group_name','entry','via','family','lower','upper')[:MAX_MATCHES+1])
    if len(ranges)>MAX_MATCHES:
        result['ip_status']='partial';notes.append('IP match limit reached; additional references remain unknown.')
    groups={}
    for row in ranges[:MAX_MATCHES]:
        edge=groups.setdefault(row['group_path'],{'path':row['group_path'],'name':row['group_name'],
            'reference_basis':'ip_subnet','membership_source':'saved_snapshot','ip_checked_at':stamp,'ip_matches':[],'ip_match_count':0})
        for ip in unique:
            if ip['family']==row['family'] and row['lower']<=ip['address']<=row['upper']:
                match={'ip':ip['ip'],'entry':row['entry'],'via_groups':row['via']}
                if match not in edge['ip_matches']:
                    edge['ip_match_count']+=1
                    if len(edge['ip_matches'])<MAX_EVIDENCE:edge['ip_matches'].append(match)
                    else:edge['ip_matches_truncated']=True
    return finish(groups)


def read(snapshot_id,ordinal,page=0):
    groups,metadata=context(snapshot_id,ordinal)
    items=sorted(groups.values(),key=lambda item:(item['name'].casefold(),item['path']))
    return {'items':items[page*25:page*25+25],'has_next':len(items)>(page+1)*25,'page':page,
        'source':'saved_snapshot','status':metadata['ip_status'],'checked_at':metadata['ip_checked_at'],
        'empty_message':('No explicit IP group definitions were saved.' if metadata['ip_status']=='not_applicable' else
            'No usable VM addresses were saved; IP matches are unknown.' if metadata['ip_status']=='not_collected' else
            'No IP matches found in the available evidence; additional matches remain unknown.' if metadata['ip_status']=='partial' else
            'No saved IP group entries match this VM\'s saved addresses.'),
        'saved_count':len(items),'notes':metadata['ip_notes']}
