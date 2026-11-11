"""Saved network references through exact VM/VIF/Policy-port identities.

Parent group effective ports are authoritative. Child criteria and tag matches
never manufacture parent membership. These links are not effective firewall policy
and are never used to establish empty-group/deletion eligibility.
"""
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import re
import threading
import time

from .resolved_membership import member_record, vm_identities

LOG = logging.getLogger('nsx_inventory')
PREFIX = r'/(?:infra|global-infra|orgs/[^/]+/projects/[^/]+/infra)'
SEGMENT = re.compile(PREFIX + r'/segments/[^/]+$')
PORT = re.compile(PREFIX + r'/segments/[^/]+/ports/[^/]+$')
ENDPOINTS = ('logical-switches', 'logical-ports', 'ip-addresses')
MODERN = {'logical-switches': 'segments', 'logical-ports': 'segment-ports', 'ip-addresses': 'ip-addresses'}


def requires_network(group, inventory, visiting=None):
    """Select network criteria, including composed parents, without evaluating them."""
    visiting = set(visiting or ())
    path = group.get('path')
    if path in visiting or len(visiting) >= 64:
        return True  # Unsupported/cyclic scope must not silently become complete zero.
    visiting.add(path)
    def visit(value):
        if isinstance(value, list):
            return any(visit(child) for child in value)
        if not isinstance(value, dict):
            return False
        if value.get('member_type') in ('Segment','SegmentPort','LogicalSwitch','LogicalPort','VIF','VirtualNetworkInterface'):
            return True
        if value.get('resource_type') == 'PathExpression':
            if not isinstance(value.get('paths',[]),list):
                return True
            for target in value.get('paths', []):
                if not isinstance(target, str):
                    return True
                if SEGMENT.fullmatch(target) or PORT.fullmatch(target):
                    return True
                if '/groups/' in target and (target not in inventory or requires_network(inventory[target], inventory, visiting)):
                    return True
        return visit(value.get('expressions', []))
    return visit(group.get('expression', []))


def page_list(client, path, *, initial=None, initial_size=1000, testing=False, endpoint=None):
    """Strict, bounded pagination; keep positives on failure, never invent zero."""
    from .collector import AuditError
    from .request_pacing import CollectionDeadlineExceeded
    result = {'status':'unavailable', 'items':[], 'reported_count':None, 'notes':[],
              'checked_at':datetime.now(timezone.utc).isoformat(), 'requests':0}
    rows, cursors, expected = {}, set(), None
    params = {'page_size':1 if testing else 1000}
    try:
        for number in range(1 if testing else 100):
            if number == 0 and initial is not None:
                page, requested_size = initial, initial_size
            else:
                result['requests'] += 1
                page, requested_size = client.get(path, params), params['page_size']
            if not isinstance(page, dict) or not isinstance(page.get('results'), list):
                raise ValueError('Missing network results array.')
            count = page.get('result_count')
            if count is not None:
                if type(count) is not int or count < 0:
                    raise ValueError('Invalid network result total.')
                if expected is not None and expected != count:
                    result['notes'].append('Network result total changed during pagination.')
                if expected is None:
                    expected = count
            result['reported_count'] = expected
            for value in (page['results'][:1] if testing else page['results']):
                if not isinstance(value, dict) and endpoint != 'ip-addresses':
                    raise ValueError('Invalid network inventory record.')
                if endpoint:
                    if isinstance(value, dict):
                        value = dict(value)
                        if value.get('target_id') and not any(value.get(k) for k in ('external_id','id','path','realization_specific_identifier')):
                            value['id'] = value['target_id']
                        if not value.get('display_name') and value.get('target_display_name'):
                            value['display_name'] = value['target_display_name']
                        if value.get('is_valid') in (False, 'false'):
                            raise ValueError('Invalid network member reference.')
                    row = member_record(value, endpoint)
                    key = row['identity']
                else:
                    row = value
                    key = value.get('path') or value.get('external_id') or value.get('id')
                    if not isinstance(key, str) or not key:
                        raise ValueError('Network inventory record has no identity.')
                if key in rows:
                    result['notes'].append('Duplicate network identity during pagination.')
                rows[key] = row
            cursor = page.get('cursor')
            if testing:
                result['notes'].append('Testing sample; network lists are incomplete.')
                break
            if not cursor:
                if expected is not None and expected != len(rows):
                    result['notes'].append('Returned network identities differ from the reported total.')
                if expected is None and len(page['results']) >= requested_size:
                    result['notes'].append('Full network page without a total or continuation.')
                result['status'] = 'partial' if result['notes'] else 'complete'
                break
            if not isinstance(cursor, str) or cursor in cursors:
                raise ValueError('Invalid or repeated network cursor.')
            cursors.add(cursor)
            params = dict(params, cursor=cursor)
        else:
            result['notes'].append('Network page limit reached (100 pages).')
    except CollectionDeadlineExceeded:
        raise
    except (AuditError, TimeoutError, ValueError) as exc:
        code = getattr(exc, 'status_code', None)
        result['http_status'] = code
        result['notes'].append(f'Network read incomplete (HTTP {code}).' if code else 'Network identities or pagination could not be verified.')
    result['items'] = list(rows.values())
    if rows and result['status'] != 'complete':
        result['status'] = 'partial'
    return result


def collect(client, groups, vms, *, probes=None, workers=4, testing=False, resources=()):
    """Read each selected parent/category and each relevant segment inventory once."""
    started = time.monotonic()
    inventory = {g['path']:g for g in groups}
    targets = [g for g in groups if requires_network(g, inventory)]
    if testing:
        targets = targets[:1]
    metadata = {'version':1, 'scope':'Visible parent-group effective ports and saved VM/VIF/Policy-port attachments; default enforcement point',
                'vms':{}, 'groups':{}, 'status':'not_applicable', 'checked_at':datetime.now(timezone.utc).isoformat()}
    stats = {'groups':len(targets), 'requests':0, 'segment_inventories':0, 'reused_probe_pages':0,
             'empty_response_checks':0, 'empty_response_recoveries':0}
    if not targets:
        return metadata, stats
    LOG.info('Network membership collection: groups=%d categories=segments,ports,IP addresses', len(targets))
    lock, modes, denied, unavailable_variants = threading.Lock(), {}, set(), {}
    progress = {'completed':0, 'last_log':time.monotonic()}
    def members(task):
        group, endpoint = task
        scope = group['path'].split('/domains/',1)[0]
        probe = (probes or {}).get(group['path'])
        initial = probe.pages.get(endpoint) if probe else None
        if not isinstance(initial,dict) or not isinstance(initial.get('results'),list):
            initial = None
        if initial and endpoint=='logical-ports' and any(
            not isinstance(row,dict) or not isinstance(row.get('path'),str) or not PORT.fullmatch(row['path'])
            for row in initial.get('results',[])):
            initial = None  # Need Policy identities, not opaque realized port IDs.
        uri = group['path']+'/members/'+endpoint
        variant = 'legacy'
        if initial is not None:
            result = page_list(client,uri,initial=initial,initial_size=probe.page_sizes.get(endpoint,1),testing=testing,endpoint=endpoint)
        elif (scope,endpoint) in denied:
            result = {'status':'unavailable','items':[],'reported_count':None,'notes':['Network category API denied for this scope.'],'requests':0}
        else:
            modern = modes.get((scope,endpoint),'modern') == 'modern'
            variant = 'modern' if modern else 'legacy'
            uri = group['path']+'/members/'+(MODERN[endpoint] if modern else endpoint)
            result = page_list(client,uri,testing=testing,endpoint=endpoint)
            if modern and MODERN[endpoint] != endpoint and result.get('http_status') in (400,404,405,501) and not result['items']:
                unavailable_variants[(scope,endpoint,MODERN[endpoint])] = result['http_status']
                failed_requests = result['requests']
                result = page_list(client,group['path']+'/members/'+endpoint,testing=testing,endpoint=endpoint)
                result['requests'] += failed_requests
                modes[(scope,endpoint)] = 'legacy'
                variant = 'legacy'
        # A supported endpoint can return an empty typed list even when its
        # compatibility endpoint has members. Verify only empty network lists;
        # positive lists retain the existing shared fast path.
        if (not testing and endpoint in ('logical-switches','logical-ports')
                and result['status']=='complete' and not result['items']):
            alternate = endpoint if variant=='modern' else MODERN[endpoint]
            prior_failure = unavailable_variants.get((scope,endpoint,alternate))
            check = ({'status':'unavailable','items':[],'requests':0,'http_status':prior_failure}
                     if prior_failure else page_list(client,group['path']+'/members/'+alternate,endpoint=endpoint))
            if check.get('http_status') in (400,404,405,501,401,403):
                unavailable_variants[(scope,endpoint,alternate)] = check['http_status']
            with lock:
                stats['empty_response_checks'] += int(not prior_failure)
                stats['empty_response_recoveries'] += int(bool(check['items']))
            requests = result['requests']+check['requests']
            if check['items']:
                result = check
                result['compatibility_recovered'] = True
                # Learn compatibility once per scope/category, before fan-out.
                modes[(scope,endpoint)] = 'legacy' if variant=='modern' else 'modern'
            elif check['status']!='complete' and check.get('http_status') not in (400,404,405,501):
                result['status'] = 'partial'
                result['notes'].append('Empty network response could not be verified against the compatibility endpoint.')
            result['requests'] = requests
        if result.get('http_status') in (401,403):
            denied.add((scope,endpoint))
        with lock:
            stats['requests'] += result['requests']
            stats['reused_probe_pages'] += int(initial is not None)
            progress['completed'] += 1
            if time.monotonic()-progress['last_log']>=30:
                LOG.info('Network member list progress: completed=%d total=%d requests=%d elapsed_seconds=%.1f',
                         progress['completed'],len(targets)*len(ENDPOINTS),stats['requests'],time.monotonic()-started)
                progress['last_log']=time.monotonic()
        return group['path'], endpoint, result
    # Probe category compatibility/permissions before parallel fan-out.
    tasks = [(g,e) for g in targets for e in ENDPOINTS]
    first, rest, seen = [], [], set()
    for task in tasks:
        key = (task[0]['path'].split('/domains/',1)[0],task[1])
        (rest if key in seen else first).append(task); seen.add(key)
    results = [members(task) for task in first]
    with ThreadPoolExecutor(max_workers=1 if testing else workers) as pool:
        results.extend(pool.map(members,rest))
    for path,endpoint,result in results:
        metadata['groups'].setdefault(path, {})[endpoint] = result
    metadata['member_list_status'] = 'complete' if all(result['status']=='complete' for _,_,result in results) else 'partial'
    stats['member_lists'] = {endpoint:dict(Counter(result['status'] for _,category,result in results if category==endpoint)) for endpoint in ENDPOINTS}
    segments = set()
    for endpoints in metadata['groups'].values():
        for row in endpoints['logical-switches']['items']:
            if isinstance(row.get('path'),str) and SEGMENT.fullmatch(row['path']):
                segments.add(row['path'])
        for row in endpoints['logical-ports']['items']:
            if isinstance(row.get('path'),str) and PORT.fullmatch(row['path']):
                segments.add(row['path'].rsplit('/ports/',1)[0])
    vif_result = page_list(client,'/api/v1/fabric/vifs',testing=testing)
    stats['requests'] += vif_result['requests']
    aliases = set().union(*(vm_identities(vm) for vm in vms)) if vms else set()
    fabric = None
    if any(vif.get('owner_vm_id') not in aliases for vif in vif_result['items'] if isinstance(vif.get('owner_vm_id'),str)):
        # Some managers use a fabric VM ID as owner, while Policy inventory uses
        # an external ID. Bridge once through exact fabric identities, not names.
        fabric = page_list(client,'/api/v1/fabric/virtual-machines',testing=testing)
        stats['requests'] += fabric['requests']
    stats['fabric_vm_bridge_reads'] = fabric['requests'] if fabric else 0
    required = defaultdict(set)
    for endpoints in metadata['groups'].values():
        for member in endpoints['logical-ports']['items']:
            path=member.get('path')
            if isinstance(path,str) and PORT.fullmatch(path):
                required[path.rsplit('/ports/',1)[0]].add(path)
    configs = {}
    for resource in resources:
        path=resource.get('path')
        if resource.get('resource_type')=='SegmentPort' and isinstance(path,str) and PORT.fullmatch(path) and not resource.get('marked_for_delete'):
            configs[path] = resource if path not in configs or configs[path]==resource else None
    reusable = {segment for segment in segments if required[segment] and all(configs.get(path) for path in required[segment])}
    def ports(segment):
        if segment in reusable:
            return segment, {'status':'complete','items':[configs[path] for path in sorted(required[segment])], 'requests':0}
        return segment, page_list(client,segment+'/ports',testing=testing)
    with ThreadPoolExecutor(max_workers=1 if testing else workers) as pool:
        port_results = dict(pool.map(ports,sorted(segments)))
    stats['segment_inventories'] = len(port_results)-len(reusable)
    stats['reused_segment_inventories'] = len(reusable)
    stats['requests'] += sum(r['requests'] for r in port_results.values())
    metadata['inventory_status'] = ('complete' if vif_result['status']=='complete'
        and all(r['status']=='complete' for r in port_results.values()) and (not fabric or fabric['status']=='complete') and not testing else 'partial')
    # Existing verified configuration search can supply ports outside the selected
    # segments, avoiding extra segment reads just to resolve unrelated interfaces.
    selected = set(port_results)
    other_ports = [p for p in resources if p.get('resource_type')=='SegmentPort'
                   and isinstance(p.get('path'),str) and p['path'].rsplit('/ports/',1)[0] not in selected]
    _bind(metadata, vms, vif_result['items'], [p for r in port_results.values() for p in r['items']] + other_ports,
          fabric_vms=fabric['items'] if fabric else ())
    stats.update(seconds=round(time.monotonic()-started,3), associations=sum(len(v['items']) for v in metadata['vms'].values()),
                 status=metadata['status'], vm_statuses=dict(Counter(v['status'] for v in metadata['vms'].values())))
    metadata['diagnostics'] = stats
    LOG.info('Network membership complete: %s',stats)
    return metadata, stats


def _bind(metadata, vms, vifs, ports, *, fabric_vms=()):
    """Join exact, unambiguous IDs; keep direct and network relationships distinct."""
    aliases, attachments, by_path = defaultdict(set), defaultdict(list), {}
    for vm in vms:
        if vm.get('marked_for_delete'):
            continue
        key = vm.get('path') or vm.get('external_id') or vm.get('id')
        if not isinstance(key,str):
            continue
        metadata['vms'][key] = {'status':'partial','items':[], 'notes':[]}
        for alias in vm_identities(vm):
            aliases[alias].add(key)
    additions = defaultdict(set)
    for vm in fabric_vms:
        if vm.get('marked_for_delete'):
            continue
        ids = vm_identities(vm)
        matches = set().union(*(aliases.get(identity,set()) for identity in ids))
        # Conflicting records accumulate multiple owners and remain ambiguous.
        if matches:
            for identity in ids:
                additions[identity].update(matches)
    for identity,owners in additions.items():
        aliases[identity].update(owners)
    invalid_inventory = any(len(owners)>1 for owners in aliases.values())
    for port in ports:
        path = port.get('path')
        if port.get('marked_for_delete'):
            continue
        if not isinstance(path,str) or not PORT.fullmatch(path):
            invalid_inventory = True
            continue
        if path in by_path and by_path[path] != port:
            invalid_inventory = True
            by_path[path] = None
            continue
        by_path[path] = port
    for path,port in by_path.items():
        attachment = port.get('attachment') if port else None
        if isinstance(attachment,dict) and isinstance(attachment.get('id'),str) and attachment['id']:
            attachments[attachment['id']].append(port)
    parent_ports = defaultdict(list)
    unresolved_segments = set()
    for group_path,endpoints in metadata['groups'].items():
        if not endpoints['logical-ports']['items']:
            unresolved_segments.update(row['path'] for row in endpoints['logical-switches']['items']
                if isinstance(row.get('path'),str) and SEGMENT.fullmatch(row['path']))
        for row in endpoints['logical-ports']['items']:
            path = row.get('path')
            if not isinstance(path,str) or not PORT.fullmatch(path):
                invalid_inventory = True
                continue
            parent_ports[path].append(group_path)
            if path not in by_path:
                invalid_inventory = True
    edges, uncertain = defaultdict(dict), set()
    attachment_owners, interface_links = defaultdict(set), defaultdict(set)
    for vif in vifs:
        if vif.get('marked_for_delete'):
            continue
        owner_id = vif.get('owner_vm_id')
        owners = aliases.get(owner_id,set()) if isinstance(owner_id,str) else set()
        attachment = vif.get('lport_attachment_id') or vif.get('external_id')
        if isinstance(attachment,str):
            attachment_owners[attachment].update(owners)
        if isinstance(vif.get('external_id'),str):
            interface_links[vif['external_id']].update((owner,attachment if isinstance(attachment,str) else None) for owner in owners)
    for vif in vifs:
        if vif.get('marked_for_delete'):
            continue
        owner_id = vif.get('owner_vm_id')
        owners = aliases.get(owner_id,set()) if isinstance(owner_id,str) else set()
        if len(owners)!=1:
            invalid_inventory = True
            continue
        owner = next(iter(owners))
        attachment = vif.get('lport_attachment_id') or vif.get('external_id')
        if (isinstance(attachment,str) and len(attachment_owners[attachment])>1
                or isinstance(vif.get('external_id'),str) and len(interface_links[vif['external_id']])>1):
            invalid_inventory=True
            uncertain.add(owner)
            continue
        candidates = attachments.get(attachment,[]) if isinstance(attachment,str) else []
        if len(candidates)!=1:
            # An interface outside the selected network segments can be irrelevant;
            # a missing attachment cannot prove a complete negative for this VM.
            uncertain.add(owner)
            continue
        port = candidates[0]
        if port['path'].rsplit('/ports/',1)[0] in unresolved_segments:
            # A VM is attached to a reported member segment, but neither typed
            # port list supplied its group edge. Keep unknown, never complete zero
            # or inferred membership through arbitrary parent AND/OR criteria.
            invalid_inventory = True
            uncertain.add(owner)
        for group_path in parent_ports.get(port['path'],[]):
            edge = edges[owner].setdefault(group_path, {'path':group_path, 'name':group_path.rsplit('/',1)[-1],
                'membership_basis':'network_attachment', 'via':[]})
            via = {'interface_id':vif.get('external_id') or vif.get('id') or attachment,
                   'attachment_id':attachment, 'port_path':port['path'],
                   'segment_path':port['path'].rsplit('/ports/',1)[0]}
            if via not in edge['via']:
                edge['via'].append(via)
    full = metadata['inventory_status']=='complete' and not invalid_inventory and all(
        endpoints['logical-ports']['status']=='complete' for endpoints in metadata['groups'].values())
    for owner,record in metadata['vms'].items():
        record['status'] = 'complete' if full and owner not in uncertain else 'partial'
        record['items'] = sorted(edges[owner].values(),key=lambda e:e['path'])
        record['notes'] = ['Network references use parent-group effective ports and exact VM/VIF/port attachment identities. They are not direct VM membership or effective firewall policy.']
        if record['status']!='complete':
            record['notes'].append('Network reference coverage is incomplete; missing relationships remain unknown.')
    metadata['status'] = 'complete' if full and not uncertain else 'partial'


def apply(report_groups, metadata, vms):
    """Publish full category lists and a separate attached-VM projection."""
    by_vm = {v.get('path') or v.get('external_id') or v.get('id'):v for v in vms}
    inverse = defaultdict(list)
    for owner,record in metadata.get('vms',{}).items():
        vm = by_vm[owner]
        for edge in record['items']:
            inverse[edge['path']].append({'identity':owner,'vm_path':owner,'name':vm.get('display_name') or owner,
                'external_id':vm.get('external_id'), 'via':edge['via']})
    names = {g['path']:g['name'] for g in report_groups}
    for record in metadata.get('vms',{}).values():
        for edge in record['items']:
            edge['name'] = names.get(edge['path'],edge['name'])
    for group in report_groups:
        endpoints = metadata.get('groups',{}).get(group['path'])
        if endpoints is None:
            continue
        group['network_membership_version'] = 1
        if group.get('membership')=='empty' and any(
                endpoint.get('compatibility_recovered') and endpoint['items'] for endpoint in endpoints.values()):
            # An authoritative positive list supersedes an earlier empty probe.
            # Network absence is never used to establish deletion eligibility.
            group['membership'] = 'nonempty'
            group['notes'] = ['Network members recovered through a compatibility endpoint; the earlier empty probe was superseded.']
        evidence = group.setdefault('resolved_membership',{'version':1,'endpoints':{}})
        evidence['checked_at'] = metadata['checked_at']
        evidence['endpoints'].update(endpoints)
        evidence['endpoints']['network-virtual-machines'] = {'status':metadata['status'],
            'items':sorted(inverse[group['path']],key=lambda r:(r['name'].casefold(),r['identity'])),
            'reported_count':None, 'notes':['VMs linked through effective member ports and saved interface attachments; not direct VM members.']}


def enrich_vm(row, metadata):
    """One unique related-group total, with direct and attachment evidence retained."""
    if metadata.get('version') != 1:
        return
    observation = metadata.get('vms',{}).get(row['path'],{})
    network_status = observation.get('status', 'not_applicable' if metadata['status']=='not_applicable' else 'unavailable')
    direct_status = row['resolved_membership_status']
    groups = {edge['path']:dict(edge,membership_basis='direct_vm') for edge in row['resolved_groups']}
    row.update(network_membership_version=1, direct_membership_status=direct_status,
        direct_group_count=row['resolved_group_count'], network_membership_status=network_status,
        network_groups=observation.get('items',[]), network_membership_notes=observation.get('notes',[]),
        network_membership_checked_at=metadata.get('checked_at'))
    for edge in row['network_groups']:
        if edge['path'] in groups:
            groups[edge['path']].update(membership_basis='direct_and_network', via=edge['via'])
        else:
            groups[edge['path']] = dict(edge)
    complete = direct_status=='complete' and network_status in ('complete','not_applicable')
    row['resolved_membership_status'] = 'complete' if complete else 'partial' if groups or direct_status=='partial' or network_status=='partial' else 'unavailable'
    row['resolved_groups'] = sorted(groups.values(),key=lambda edge:(edge['name'].casefold(),edge['path']))
    row['network_group_count'] = len(row['network_groups']) if network_status in ('complete','partial','not_applicable') else None
    row['resolved_group_count'] = len(groups) if complete or groups or row['resolved_membership_status']=='partial' else None
    row['resolved_membership_notes'] = list(row.get('resolved_membership_notes',[]))+row['network_membership_notes']
