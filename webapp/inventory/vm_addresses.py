"""Save observed VIF addresses by exact VM owner, without membership inference."""
from collections import defaultdict, Counter


def save(vms, vifs, aliases, *, complete, checked_at):
    owners_by_interface = defaultdict(set)
    by_interface, invalid = {}, set()
    incomplete = not complete
    for vif in vifs:
        if vif.get('marked_for_delete'):
            continue
        identity = vif.get('external_id') or vif.get('id')
        owner_id = vif.get('owner_vm_id')
        owners = aliases.get(owner_id, set()) if isinstance(owner_id, str) else set()
        if not isinstance(identity, str) or not identity or len(owners) != 1:
            incomplete = True
            invalid.update(owners)
            continue
        owner = next(iter(owners))
        if identity in by_interface and by_interface[identity] != vif:
            invalid.add(owner)
            invalid.update(owners_by_interface[identity])
            incomplete = True
        owners_by_interface[identity].add(owner)
        by_interface[identity] = vif
    rows = defaultdict(list)
    for identity, vif in sorted(by_interface.items()):
        owners = owners_by_interface[identity]
        if len(owners) != 1 or owners & invalid:
            incomplete = True
            continue
        owner = next(iter(owners))
        info = vif.get('ip_address_info', [])
        if not isinstance(info, list) or any(not isinstance(item, dict)
                or not isinstance(item.get('ip_addresses'), list)
                or any(not isinstance(ip, str) for ip in item['ip_addresses']) for item in info):
            invalid.add(owner)
            incomplete = True
            continue
        rows[owner].append({**{key: vif[key] for key in
            ('external_id', 'id', 'owner_vm_id', 'lport_attachment_id') if key in vif},
            'ip_address_info': [{'ip_addresses': list(dict.fromkeys(item['ip_addresses'])),
                                 'source': item.get('source', 'Not recorded')} for item in info]})
    states = Counter()
    with_addresses = interfaces = 0
    for vm in vms:
        if vm.get('marked_for_delete'):
            continue
        owner = vm.get('path') or vm.get('external_id') or vm.get('id')
        if not isinstance(owner, str):
            continue
        saved = rows.get(owner, []) if owner not in invalid else []
        # Collection coverage qualifies positive observations too. Shared IP
        # ownership across VM records is checked again by the browsing index.
        status = 'partial' if incomplete or owner in invalid else 'complete'
        vm['network_interfaces'] = saved
        vm['ip_address_evidence'] = {'version': 1, 'status': status,
            'source': 'fabric_vifs', 'checked_at': checked_at, 'interface_count': len(saved)}
        interfaces += len(saved)
        with_addresses += int(any(item['ip_addresses'] for row in saved for item in row['ip_address_info']))
        states[status] += 1
    return {'vms': sum(states.values()), 'vms_with_reported_addresses': with_addresses,
            'saved_interfaces': interfaces, 'statuses': dict(states),
            'source': 'shared_fabric_vifs', 'per_vm_requests': 0}
