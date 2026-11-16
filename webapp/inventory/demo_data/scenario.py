"""Generate synthetic NSX Policy inventories for demonstration collections.

Each ``Inventory`` is the NSX state at one simulated collection time. Inventories of
one profile differ slightly from collection to collection (scale-out, a removed VM,
an onboarded application, a changed rule and changing counters) so comparisons and
observation-based findings have something to show. Names, addresses (RFC 1918 and
RFC 5737 documentation ranges) and identifiers are generated and obviously
synthetic.
"""
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import uuid

DOMAIN = '/infra/domains/default'
GROUPS = DOMAIN + '/groups/'
POLICIES = DOMAIN + '/security-policies/'
SERVICES = '/infra/services/'
SEGMENTS = '/infra/segments/'
VM_ROOT = '/infra/realized-state/enforcement-points/default/virtual-machines/'
ENFORCEMENT_POINT = '/infra/sites/default/enforcement-points/default'
NAMESPACE = uuid.UUID('0c5e1d2a-9b7f-4e3c-8a61-5d0e0de0da7a')
TIERS = (('web', 1), ('app', 2), ('db', 3))
INFRA_ROLES = (('dns', 2), ('ntp', 2), ('jump', 3), ('backup', 2), ('mon', 3), ('log', 2))
DATABASE_SERVICES = ('MS-SQL-S', 'MySQL', 'SVC-POSTGRESQL')
AND = {'resource_type': 'ConjunctionOperator', 'conjunction_operator': 'AND'}


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    host: str
    env_tag: str
    code: str
    prefix: str
    net: int
    apps: tuple
    snapshot_days: tuple          # Days before the load time, oldest first.
    late_apps: tuple = ()         # Onboarded during the final four collections.
    partial_index: int = None     # Collection with incomplete VM/interface coverage.
    failures: tuple = ()          # (days before load time, failure mode)
    cancelled: tuple = ()         # Days before load time of a stopped collection.
    legacy_groups: int = 20
    special_groups: bool = False  # Identity and unreadable-membership examples.
    hosts: str = 'app,db,mgmt'    # VM tiers that receive single-address host groups.

    @property
    def slug(self):
        return 'demo-' + self.key

    @property
    def manager(self):
        return 'https://' + self.host


PROFILES = (
    Profile('production', 'Production (demo)', 'nsx-production.example.invalid', 'production', 'PRD', 'prd', 10,
            (('orion', 10), ('lyra', 8), ('vega', 8), ('atlas', 7), ('nova', 10), ('helix', 7), ('pulsar', 8),
             ('quasar', 7), ('zephyr', 8), ('meridian', 8), ('solstice', 7), ('cobalt', 7), ('kestrel', 6)),
            (34, 30, 26, 22, 18, 14, 10, 6, 3, 0.08), late_apps=('kestrel',), partial_index=4,
            failures=((20.5, 'unauthorized'),), legacy_groups=60, special_groups=True),
    Profile('disaster-recovery', 'Disaster recovery (demo)', 'nsx-recovery.example.invalid', 'dr', 'DR', 'dr', 20,
            (('orion', 5), ('lyra', 4), ('vega', 4), ('nova', 5), ('pulsar', 4), ('meridian', 4)),
            (20, 13, 6, 0.1), legacy_groups=24, cancelled=(9.5,), hosts='db,mgmt'),
    Profile('lab', 'Lab (demo)', 'nsx-lab.example.invalid', 'lab', 'LAB', 'lab', 30,
            (('orion', 3), ('vega', 3), ('cobalt', 2), ('sandbox', 3)),
            (8, 4, 0.15), legacy_groups=12, failures=((5.5, 'unavailable'),), hosts='mgmt'),
)


def ident(*parts):
    return str(uuid.uuid5(NAMESPACE, '/'.join(str(part) for part in parts)))


def number(*parts, low=0, high=1000):
    digest = int(hashlib.sha256('/'.join(str(p) for p in parts).encode()).hexdigest()[:12], 16)
    return low + digest % (high - low + 1)


def condition(scope, value):
    return {'resource_type': 'Condition', 'member_type': 'VirtualMachine', 'key': 'Tag',
            'operator': 'EQUALS', 'value': scope + '|' + value}


def name_condition(operator, value):
    return {'resource_type': 'Condition', 'member_type': 'VirtualMachine', 'key': 'Name',
            'operator': operator, 'value': value}


def all_of(*conditions):
    result = []
    for item in conditions:
        if result:
            result.append(dict(AND))
        result.append(item)
    return result


def path_expression(*paths):
    return {'resource_type': 'PathExpression', 'paths': list(paths)}


def address_expression(*addresses):
    return {'resource_type': 'IPAddressExpression', 'ip_addresses': list(addresses)}


@dataclass
class Inventory:
    profile: Profile
    index: int
    count: int
    anchor: object
    vms: list = field(default_factory=list)
    vifs: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    ports: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    services: list = field(default_factory=list)
    policies: list = field(default_factory=list)
    rules: dict = field(default_factory=dict)
    counters: dict = field(default_factory=dict)
    other_resources: list = field(default_factory=list)
    unreadable_groups: set = field(default_factory=set)
    unsupported_statistics: set = field(default_factory=set)
    addresses: dict = field(default_factory=dict)

    @property
    def partial(self):
        return self.profile.partial_index == self.index

    @property
    def latest(self):
        return self.index == self.count - 1

    def created(self, *parts, days=None):
        days = number('created', *parts, low=60, high=380) if days is None else days
        return int((self.anchor - timedelta(days=days, minutes=number('minute', *parts, high=1400))).timestamp() * 1000)

    def finalize(self):
        self.group_by_path = {g['path']: g for g in self.groups}
        self.vm_by_external = {vm['external_id']: vm for vm in self.vms}
        self.port_by_path = {port['path']: port for port in self.ports}
        self.ports_by_segment, self.port_by_attachment = {}, {}
        for port in self.ports:
            self.ports_by_segment.setdefault(port['parent_path'], []).append(port)
            self.port_by_attachment[port['attachment']['id']] = port
        self.port_owner = {self.port_by_attachment[vif['lport_attachment_id']]['path']: vif['owner_vm_id']
                           for vif in self.vifs}
        self.member_cache = {}
        self.associations = {}
        for group in self.groups:
            for external in self.vm_members(group['path']):
                self.associations.setdefault(external, []).append(group)
        counts = {}
        for record in self.vms + self.groups + self.segments + self.other_resources:
            for tag in record.get('tags', []):
                counts[(tag['scope'], tag['tag'])] = counts.get((tag['scope'], tag['tag']), 0) + 1
        self.tags = [{'scope': scope, 'tag': tag, 'tagged_objects_count': count}
                     for (scope, tag), count in sorted(counts.items())]
        if self.profile.special_groups:  # A catalog-only tag whose use cannot be classified.
            self.tags.append({'scope': 'owner', 'tag': 'team-legacy', 'tagged_objects_count': 0})


    def matches(self, node, vm, seen):
        kind = node.get('resource_type')
        if kind == 'Condition':
            if node.get('member_type') != 'VirtualMachine':
                return False
            value, operator = node.get('value', ''), node.get('operator')
            if node.get('key') == 'Tag':
                scope, _, tag = value.rpartition('|')
                return any(t['tag'] == tag and (not scope or t['scope'] == scope) for t in vm['tags'])
            name = vm['display_name']
            return {'EQUALS': name == value, 'STARTSWITH': name.startswith(value), 'CONTAINS': value in name,
                    'ENDSWITH': name.endswith(value)}.get(operator, False)
        if kind == 'NestedExpression':
            return self.evaluate(node.get('expressions', []), vm, seen)
        if kind == 'PathExpression':
            return any(path in self.group_by_path and vm['external_id'] in self.vm_members(path, seen)
                       for path in node.get('paths', []))
        if kind == 'ExternalIDExpression':
            return vm['external_id'] in node.get('external_ids', [])
        return False


    def evaluate(self, expression, vm, seen):
        result, operator = None, 'OR'
        for node in expression:
            if node.get('resource_type') == 'ConjunctionOperator':
                operator = node.get('conjunction_operator', 'OR')
                continue
            value = self.matches(node, vm, seen)
            result = value if result is None else (result and value if operator == 'AND' else result or value)
        return bool(result)


    def vm_members(self, path, seen=frozenset()):
        """External IDs of direct VM members; segment criteria never add direct VMs."""
        if path in self.member_cache:
            return self.member_cache[path]
        group = self.group_by_path.get(path)
        if group is None or path in seen or group.get('_system_owned'):
            return set()
        members = {vm['external_id'] for vm in self.vms if self.evaluate(group.get('expression', []), vm, seen | {path})}
        self.member_cache[path] = members
        return members


    def segment_paths(self, path, seen=frozenset()):
        group = self.group_by_path.get(path)
        if group is None or path in seen:
            return set()
        result = set()
        for node in group.get('expression', []):
            if node.get('resource_type') == 'PathExpression':
                for target in node.get('paths', []):
                    if target.startswith(SEGMENTS):
                        result.add(target)
                    elif target in self.group_by_path:
                        result |= self.segment_paths(target, seen | {path})
        return result


    def port_members(self, path):
        """Effective member ports: ports on member segments plus ports of member VMs."""
        ports = {}
        for segment in self.segment_paths(path):
            for port in self.ports_by_segment.get(segment, []):
                ports[port['path']] = port
        members = self.vm_members(path)
        for port_path, owner in self.port_owner.items():
            if owner in members:
                ports[port_path] = self.port_by_path[port_path]
        return [ports[key] for key in sorted(ports)]


    def ip_members(self, path):
        group = self.group_by_path.get(path) or {}
        values = set()
        for node in group.get('expression', []):
            if node.get('resource_type') == 'IPAddressExpression':
                values.update(node.get('ip_addresses', []))
        for port in self.port_members(path):
            owner = self.port_owner.get(port['path'])
            if owner in self.addresses:
                values.add(self.addresses[owner])
        for external in self.vm_members(path):
            values.add(self.addresses[external])
        return sorted(values)



def build(profile, index, count, anchor):
    """Return the synthetic NSX state of ``profile`` at collection ``index``."""
    inventory = Inventory(profile, index, count, anchor)
    late = index >= count - 4
    apps = [(position, app, size) for position, (app, size) in enumerate(profile.apps)
            if app not in profile.late_apps or late]
    _virtual_machines(inventory, apps)
    _groups(inventory, apps)
    _services(inventory, apps)
    _firewall(inventory, apps)
    _other_resources(inventory)
    inventory.finalize()
    return inventory


def _virtual_machines(inventory, apps):
    p, i, n = inventory.profile, inventory.index, inventory.count
    specs = []
    for position, app, size in apps:
        for tier, octet in TIERS:
            count = size if tier != 'db' else max(2, size // 2)
            if position % 3 == 0 and i >= n - 3:
                count += 1  # Scale-out during the most recent collections.
            for k in range(count):
                if position % 4 == 1 and tier == 'web' and k == count - 1 and i >= n - 2:
                    continue  # Retired web server.
                specs.append(dict(name=f'{p.prefix}-{app}-{tier}-{k + 1:02d}', segment=tier,
                                  ip=f'10.{p.net}.{octet}.{10 + position * 16 + k}',
                                  tags=[('env', p.env_tag), ('app', app), ('tier', tier)],
                                  running=(position + k) % 11 != 5, app=app))
    for position, (role, count) in enumerate(INFRA_ROLES):
        for k in range(count):
            specs.append(dict(name=f'{p.prefix}-{role}-{k + 1:02d}', segment='mgmt',
                              ip=f'10.{p.net}.9.{10 + position * 8 + k}',
                              tags=[('env', p.env_tag), ('role', role), ('tier', 'mgmt')], running=True, app=None))
    for k in range(3):
        specs.append(dict(name=f'{p.prefix}-tmp-build-{k + 1:02d}', segment='misc', ip=f'10.{p.net}.20.{11 + k}',
                          tags=[], running=k != 2, app=None))
    first, second = apps[0][1], apps[1][1]
    specs.append(dict(name=f'{p.prefix}-shared-util-01', segment='misc', ip=f'10.{p.net}.20.30',
                      tags=[('env', p.env_tag), ('app', first), ('app', second)], running=True, app=None))
    hosts = ['esx-%02d.%s' % (k + 1, p.host) for k in range(4)]
    for spec in specs:
        name = spec['name']
        external = ident(p.key, 'vm', name)
        vm = {'resource_type': 'VirtualMachine', 'display_name': name, 'external_id': external, 'id': external,
              'path': VM_ROOT + external, 'power_state': 'VM_RUNNING' if spec['running'] else 'VM_STOPPED',
              'type': 'REGULAR', 'host_id': ident(p.key, 'host', hosts[number(name, high=3)]),
              'compute_ids': ['moIdOnHost:vm-%d' % number(p.key, name, low=1000, high=9999),
                              'externalId:' + external, 'biosUuid:' + ident(p.key, 'bios', name)],
              'source': {'target_display_name': hosts[number(name, high=3)], 'target_type': 'HostNode',
                         'is_valid': True},
              'guest_info': {'os_name': 'Ubuntu Linux (64-bit)' if number('os', name) % 3 else 'Microsoft Windows Server 2022 (64-bit)',
                             'computer_name': name},
              'tags': [{'scope': scope, 'tag': tag} for scope, tag in spec['tags']],
              '_last_sync_time': inventory.created('sync', name, days=0)}
        segment = f'{p.prefix}-{spec["segment"]}-segment'
        attachment = ident(p.key, 'attachment', name)
        port_id = ident(p.key, 'port', name)
        inventory.vms.append(vm)
        inventory.vifs.append({'resource_type': 'VirtualNetworkInterface', 'external_id': external + '-4000',
                               'owner_vm_id': external, 'lport_attachment_id': attachment,
                               'device_name': 'Network adapter 1', 'display_name': name + '/Network adapter 1',
                               'mac_address': '00:50:56:%02x:%02x:%02x' % (p.net, number('mac', name, high=255), number('mac2', name, high=255)),
                               'vm_local_id_on_host': '4000',
                               'ip_address_info': [{'source': 'VM_TOOLS', 'ip_addresses': [spec['ip']]}]})
        inventory.ports.append({'resource_type': 'SegmentPort', 'id': port_id, 'display_name': name + '.vmx@' + attachment[:8],
                                'path': SEGMENTS + segment + '/ports/' + port_id, 'parent_path': SEGMENTS + segment,
                                'attachment': {'id': attachment, 'type': 'PARENT', 'traffic_tag': 0},
                                'admin_state': 'UP', '_create_time': inventory.created('port', name)})
        inventory.addresses[external] = spec['ip']
    for tier, octet in TIERS + (('mgmt', 9), ('misc', 20)):
        segment = f'{p.prefix}-{tier}-segment'
        inventory.segments.append({'resource_type': 'Segment', 'id': segment, 'display_name': segment,
                                   'path': SEGMENTS + segment, 'subnets': [{'gateway_address': f'10.{p.net}.{octet}.1/24'}],
                                   'tags': [{'scope': 'env', 'tag': p.env_tag}],
                                   'connectivity_path': f'/infra/tier-1s/t1-{p.prefix}-workloads',
                                   '_create_time': inventory.created('segment', segment)})


def _group(inventory, gid, name, expression, description='', tags=(), late=None, **extra):
    p = inventory.profile
    record = {'resource_type': 'Group', 'id': gid, 'display_name': name, 'path': GROUPS + gid,
              'parent_path': DOMAIN, 'relative_path': gid, 'description': description,
              'expression': expression, 'tags': [{'scope': scope, 'tag': tag} for scope, tag in tags],
              'unique_id': ident(p.key, 'group', gid), '_create_time': inventory.created('group', gid, days=late),
              '_system_owned': False, 'marked_for_delete': False}
    record.update(extra)
    inventory.groups.append(record)
    return record['path']


def _groups(inventory, apps):
    p, i, n = inventory.profile, inventory.index, inventory.count
    code = p.code
    env = condition('env', p.env_tag)
    late_days = p.snapshot_days[max(0, n - 4)] + 1
    app_groups, web_groups, db_groups = [], [], []
    for position, app, _ in apps:
        late = late_days if app in p.late_apps else None
        owner = ('owner', 'team-' + app)
        app_groups.append(_group(inventory, f'{code}-{app.upper()}', f'{code}-{app.upper()}',
                                 all_of(env, condition('app', app)), f'All {app} workloads', [owner], late))
        for tier, _ in TIERS:
            gid = f'{code}-{app.upper()}-{tier.upper()}'
            name = gid
            if position == 1 and tier == 'web':
                name = f'{app}_web_servers'
            elif position == 3 and tier == 'db':
                name = f'GRP-{app.upper()}-DATABASE'
            elif position == 5 and tier == 'app':
                name = f'{code}-{app.upper()}-APPLICATION'
            path = _group(inventory, gid, name, all_of(env, condition('app', app), condition('tier', tier)),
                          f'{app} {tier} tier', [owner], late)
            (web_groups if tier == 'web' else db_groups if tier == 'db' else []).append(path)
    inventory.app_groups = app_groups
    segment_groups = {}
    for tier in ('web', 'app', 'db', 'mgmt'):
        segment_groups[tier] = _group(inventory, f'{code}-SEG-{tier.upper()}', f'{code}-SEG-{tier.upper()}',
                                      [path_expression(f'{SEGMENTS}{p.prefix}-{tier}-segment')],
                                      f'Workloads attached to the {tier} segment')
    inventory.segment_groups = segment_groups
    inventory.all_web = _group(inventory, f'{code}-ALL-WEB', f'{code}-ALL-WEB', [path_expression(*web_groups)], 'Nested: every web tier')
    inventory.all_db = _group(inventory, f'{code}-ALL-DB', f'{code}-ALL-DB', [path_expression(*db_groups)], 'Nested: every database tier')
    inventory.all_apps = _group(inventory, f'{code}-ALL-APPS', f'{code}-ALL-APPS', [path_expression(*app_groups)], 'Nested: every application')
    inventory.frontend = _group(inventory, f'{code}-NET-FRONTEND', f'{code}-NET-FRONTEND',
                                [path_expression(segment_groups['web'], segment_groups['app'])],
                                'Nested segment groups: web and application networks')
    infra = {}
    for role, _ in INFRA_ROLES:
        expression = ([name_condition('STARTSWITH', f'{p.prefix}-jump')] if role == 'jump'
                      else all_of(env, condition('role', role)))
        if role == 'mon' and inventory.latest:
            expression = all_of(env, condition('role', role), condition('tier', 'mgmt'))  # Definition tightened.
        infra[role] = _group(inventory, f'{code}-INFRA-{role.upper()}', f'{code}-INFRA-{role.upper()}', expression,
                             f'Infrastructure {role} servers', [('owner', 'team-platform')])
    inventory.infra = infra
    dns = [inventory.addresses[vm['external_id']] for vm in inventory.vms if '-dns-' in vm['display_name']]
    sets = {'resolvers': ('IPS-DNS-RESOLVERS', dns), 'users': ('IPS-CORP-USERS', ['198.51.100.0/24']),
            'partners': ('IPS-PARTNER-GATEWAYS', ['192.0.2.0/28']), 'mgmt': (f'IPS-{code}-MGMT', [f'10.{p.net}.9.0/24']),
            'private': ('IPS-RFC1918', ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']),
            'nonprod': ('IPS-NONPROD-RANGES', ['10.20.0.0/16', '10.30.0.0/16']),
            'scanner': ('IPS-LEGACY-SCANNER', ['203.0.113.17']), 'old-partner': ('IPS-OLD-PARTNER', ['203.0.113.64/27'])}
    inventory.ipsets = {key: _group(inventory, gid, gid, [address_expression(*values)], 'Configured addresses')
                        for key, (gid, values) in sets.items()}
    host_tiers = set(p.hosts.split(','))
    inventory.host_groups = {'app': [], 'db': [], 'mgmt': []}
    for vm in inventory.vms:
        parts = vm['display_name'].split('-')
        tier = parts[2] if len(parts) == 4 and parts[2] in ('app', 'db') else 'mgmt' if any(
            tag['tag'] == 'mgmt' for tag in vm['tags']) else None
        if tier not in host_tiers:
            continue
        address = inventory.addresses[vm['external_id']]
        legacy = number('legacy-host', vm['display_name']) % 7 == 0
        name = 'H-' + vm['display_name'] if legacy else 'HST-' + address
        gid = 'host-' + vm['display_name']
        inventory.host_groups[tier].append(_group(inventory, gid, name, [address_expression(address)],
                                                  'Single host address'))
    words = ('TMP-PROJECT', 'OLD-MIGRATION-WAVE', 'DECOM-PILOT', 'SANDBOX-TRIAL')
    stable = [app for app, _ in p.apps if app not in p.late_apps]  # Definitions must not drift.
    for k in range(p.legacy_groups):
        kind = k % 4
        gid = f'{words[kind]}-{k + 1:02d}'
        if gid == 'TMP-PROJECT-05' and i >= n - 2:
            continue  # Removed from NSX before the last collections.
        expression = ([condition('app', stable[k % len(stable)])] if kind == 0
                      else [address_expression(f'10.{p.net}.{40 + k}.0/26')] if kind == 1
                      else [condition('project', f'pilot-{k + 1}')] if kind == 2
                      else [name_condition('STARTSWITH', f'{p.prefix}-sandbox-{k + 1}')])
        _group(inventory, gid, gid, expression, 'Legacy object kept after a finished project')
    inventory.quarantine = _group(inventory, f'{code}-QUARANTINE', f'{code}-QUARANTINE',
                                  [condition('security', 'quarantine')], 'Workloads isolated by the security team')
    retired = apps[3][1] if len(apps) > 3 else apps[-1][1]
    inventory.retired = _group(inventory, f'{code}-{retired.upper()}-V1', f'{code}-{retired.upper()}-V1',
                               all_of(env, condition('app', retired + '-v1')), 'Previous release, no workloads')
    _group(inventory, f'{code}-FUTURE-ANALYTICS', f'{code}-FUTURE-ANALYTICS', all_of(env, condition('app', 'analytics')),
           'Prepared for a planned application')
    if p.special_groups:
        _group(inventory, f'{code}-FINANCE-USERS', f'{code}-FINANCE-USERS', [], 'Identity firewall group',
               extended_expression=[{'resource_type': 'IdentityGroupExpression', 'identity_groups': [
                   {'distinguished_name': 'CN=Finance,OU=Groups,DC=example,DC=invalid',
                    'domain_base_distinguished_name': 'DC=example,DC=invalid'}]}])
        erp = _group(inventory, f'{code}-LEGACY-ERP', f'{code}-LEGACY-ERP', all_of(env, condition('app', 'erp-legacy')),
                     'Membership API returns an error for this group')
        inventory.unreadable_groups.add(erp)
    _group(inventory, 'DefaultMaliciousIpGroup', 'DefaultMaliciousIpGroup',
           [address_expression('192.0.2.200')], 'Default malicious IP group', _system_owned=True)


def _services(inventory, apps):
    p = inventory.profile
    builtin = [('HTTP', 'TCP', ['80']), ('HTTPS', 'TCP', ['443']), ('SSH', 'TCP', ['22']), ('DNS', 'TCP', ['53']),
               ('DNS-UDP', 'UDP', ['53']), ('NTP', 'UDP', ['123']), ('LDAP', 'TCP', ['389']),
               ('LDAP-over-SSL', 'TCP', ['636']), ('MS-SQL-S', 'TCP', ['1433']), ('MySQL', 'TCP', ['3306']),
               ('SNMP', 'UDP', ['161']), ('Syslog (UDP)', 'UDP', ['514']), ('RDP', 'TCP', ['3389']), ('SMB', 'TCP', ['445'])]

    def service(sid, name, protocol, ports, default=False):
        entry = {'resource_type': 'L4PortSetServiceEntry', 'id': sid + '-entry', 'display_name': sid + '-entry',
                 'l4_protocol': protocol, 'destination_ports': ports, 'source_ports': []}
        inventory.services.append({'resource_type': 'Service', 'id': sid, 'display_name': name, 'path': SERVICES + sid,
                                   'service_entries': [entry], 'is_default': default, '_system_owned': default,
                                   'unique_id': ident(p.key, 'service', sid), 'service_type': 'NON_ETHER',
                                   '_create_time': inventory.created('service', sid, days=900 if default else None)})
        return SERVICES + sid
    for name, protocol, ports in builtin:
        service(name.replace(' ', '_').replace('(', '').replace(')', ''), name, protocol, ports, True)
    inventory.services.append({'resource_type': 'Service', 'id': 'ICMP-ALL', 'display_name': 'ICMP ALL',
                               'path': SERVICES + 'ICMP-ALL', 'is_default': True, '_system_owned': True,
                               'service_entries': [{'resource_type': 'ICMPTypeServiceEntry', 'id': 'ICMP-ALL',
                                                    'protocol': 'ICMPv4'}], 'unique_id': ident(p.key, 'service', 'ICMP-ALL')})
    inventory.app_services = {}
    for position, app, _ in apps:
        api = service(f'SVC-{app.upper()}-API', f'SVC-{app.upper()}-API', 'TCP', [str(8000 + position * 10)])
        admin = service(f'SVC-{app.upper()}-ADMIN', f'SVC-{app.upper()}-ADMIN', 'TCP', [str(9000 + position * 10)])
        inventory.app_services[app] = (api, admin)
    for sid, protocol, ports in [('SVC-POSTGRESQL', 'TCP', ['5432']), ('SVC-BACKUP-AGENT', 'TCP', ['10000-10002']),
                                 ('SVC-MON-AGENT', 'TCP', ['9100']), ('SVC-SYSLOG-TLS', 'TCP', ['6514']),
                                 ('SVC-ARCHIVE-SYNC', 'TCP', ['8873']), ('SVC-LEGACY-FTP', 'TCP', ['20-21']),
                                 ('SVC-OLD-RDP-ALT', 'TCP', ['3390']), ('SVC-TEST-8081', 'TCP', ['8081']),
                                 ('SVC-PILOT-GRPC', 'TCP', ['50051'])]:
        service(sid, sid, protocol, ports)


def _firewall(inventory, apps):
    p, i, n = inventory.profile, inventory.index, inventory.count
    state = {'rule_id': 1000 + p.net * 100}

    def policy(pid, name, category, sequence, scope=('ANY',), system=False, late=None):
        path = POLICIES + pid
        inventory.policies.append({'resource_type': 'SecurityPolicy', 'id': pid, 'display_name': name, 'path': path,
                                   'parent_path': DOMAIN, 'category': category, 'sequence_number': sequence,
                                   'scope': list(scope), 'stateful': category != 'Ethernet', 'tcp_strict': False,
                                   'unique_id': ident(p.key, 'policy', pid), '_system_owned': system,
                                   '_create_time': inventory.created('policy', pid, days=late)})
        inventory.rules[path] = []
        return path

    def rule(policy_path, name, sources, destinations, services=('ANY',), action='ALLOW', scope=('ANY',),
             disabled=False, sources_excluded=False, destinations_excluded=False, rate=None, quiet_until=None,
             system=False, late=None):
        state['rule_id'] += 1
        rid = name.lower().replace(' ', '-').replace('(', '').replace(')', '').replace('/', '-')
        rules = inventory.rules[policy_path]
        record = {'resource_type': 'Rule', 'id': rid, 'display_name': name, 'path': policy_path + '/rules/' + rid,
                  'parent_path': policy_path, 'rule_id': state['rule_id'], 'sequence_number': (len(rules) + 1) * 10,
                  'source_groups': list(sources), 'destination_groups': list(destinations), 'services': list(services),
                  'profiles': ['ANY'], 'scope': list(scope), 'action': action, 'direction': 'IN_OUT',
                  'ip_protocol': 'IPV4_IPV6', 'logged': action != 'ALLOW', 'disabled': disabled,
                  'sources_excluded': sources_excluded, 'destinations_excluded': destinations_excluded,
                  'unique_id': ident(p.key, 'rule', policy_path, rid), '_system_owned': system,
                  '_create_time': inventory.created('rule', policy_path, rid, days=late)}
        rules.append(record)
        quiet = rate is None or disabled or (quiet_until is not None and i < quiet_until)
        base = 0 if quiet else rate
        hits = base * (i + 1) + (number('jitter', record['path'], i, high=max(1, base // 10)) if base else 0)
        inventory.counters[record['path']] = {'internal_rule_id': str(record['rule_id']), 'hit_count': hits,
                                              'packet_count': hits * 9, 'byte_count': hits * 7400,
                                              'session_count': hits, 'max_session_count': min(hits, 900),
                                              'popularity_index': min(100, hits // 1000)}
        return record['path']

    emergency = policy('emergency-quarantine', 'Emergency quarantine', 'Emergency', 10, scope=[inventory.quarantine])
    rule(emergency, 'Quarantine block inbound', ['ANY'], [inventory.quarantine], action='DROP')
    rule(emergency, 'Quarantine block outbound', [inventory.quarantine], ['ANY'], action='DROP')

    layer2 = policy('default-layer2-section', 'Default Layer2 Section', 'Ethernet', 1, system=True, late=900)
    rule(layer2, 'Default Layer2 Rule', ['ANY'], ['ANY'], system=True, late=900)
    inventory.unsupported_statistics.add(layer2)

    infra = policy('infrastructure-services', 'Infrastructure shared services', 'Infrastructure', 20)
    rule(infra, 'DNS to resolvers', ['ANY'], [inventory.ipsets['resolvers']], [SERVICES + 'DNS', SERVICES + 'DNS-UDP'], rate=48000)
    rule(infra, 'NTP to time servers', ['ANY'], [inventory.infra['ntp']], [SERVICES + 'NTP'], rate=9000)
    rule(infra, 'Jump hosts SSH to servers', [inventory.infra['jump']], [inventory.all_web, inventory.all_db],
         [SERVICES + 'SSH'], rate=340)
    if inventory.host_groups['db']:
        rule(infra, 'Backup agents to databases', [inventory.infra['backup']], inventory.host_groups['db'],
             [SERVICES + 'SVC-BACKUP-AGENT'], rate=1200)
    if inventory.host_groups['app']:
        rule(infra, 'Monitoring scrape application hosts', [inventory.infra['mon']], inventory.host_groups['app'],
             [SERVICES + 'SVC-MON-AGENT'], rate=26000)
    else:
        rule(infra, 'Monitoring scrape application hosts', [inventory.infra['mon']], [inventory.all_apps],
             [SERVICES + 'SVC-MON-AGENT'], rate=26000)
    rule(infra, 'Central logging', [inventory.all_apps], [inventory.infra['log']],
         [SERVICES + 'Syslog_UDP', SERVICES + 'SVC-SYSLOG-TLS'], rate=61000)
    rule(infra, 'Administrator SSH except partner networks', [inventory.ipsets['partners']], [inventory.infra['jump']],
         [SERVICES + 'SSH'], sources_excluded=True, rate=150)
    rule(infra, 'Archive synchronisation (paused)', [inventory.infra['backup']], [inventory.ipsets['mgmt']],
         [SERVICES + 'SVC-ARCHIVE-SYNC'], disabled=True)
    for tier, group in inventory.host_groups.items():
        if tier == 'mgmt' and group:
            rule(infra, 'Management hosts to directory', group, [inventory.ipsets['mgmt']],
                 [SERVICES + 'LDAP-over-SSL'], rate=800)

    environment = policy('environment-isolation', 'Environment isolation', 'Environment', 30)
    rule(environment, 'Deny non-production to this environment', [inventory.ipsets['nonprod']], [inventory.all_apps],
         action='DROP', rate=None if p.env_tag == 'production' else 20)
    rule(environment, 'Corporate users to web tiers', [inventory.ipsets['users']], [inventory.all_web],
         [SERVICES + 'HTTPS'], rate=150000)
    rule(environment, 'Block internet egress', [inventory.all_apps], [inventory.ipsets['private']],
         destinations_excluded=True, action='DROP', rate=430)
    rule(environment, 'Frontend networks east-west', [inventory.frontend], [inventory.frontend],
         [SERVICES + 'HTTPS'], rate=9800)

    database = DATABASE_SERVICES
    for position, app, _ in apps:
        late = (p.snapshot_days[max(0, n - 4)] + 1) if app in p.late_apps else None
        code = f'{p.code}-{app.upper()}'
        groups = {tier: GROUPS + f'{code}-{tier.upper()}' for tier, _ in TIERS}
        app_group = GROUPS + code
        policy_scope = [app_group] if position % 2 == 0 else ['ANY']
        if position == 1 and inventory.latest:
            policy_scope = [app_group]  # Applied To narrowed in the latest collection.
        path = policy('app-' + app, f'APP-{app.upper()}', 'Application', 100 + position * 10, policy_scope, late=late)
        rule_scope = ['ANY'] if policy_scope != ['ANY'] else [app_group]
        api, admin = inventory.app_services[app]
        rule(path, f'{app} users to web', [inventory.ipsets['users']], [groups['web']], [SERVICES + 'HTTPS'],
             scope=rule_scope, rate=number('rate', app, low=2000, high=90000), late=late)
        web_services = [api] + ([SERVICES + 'HTTPS'] if position == 4 and inventory.latest else [])
        rule(path, f'{app} web to app', [groups['web']], [groups['app']], web_services, scope=rule_scope,
             rate=number('rate-api', app, low=1500, high=60000), late=late)
        rule(path, f'{app} app to database', [groups['app']], [groups['db']],
             [SERVICES + database[position % 3]], scope=rule_scope, rate=number('rate-db', app, low=900, high=40000), late=late)
        if position % 2 == 0:
            # Unused consoles record zero hits; one starts carrying traffic late.
            rule(path, f'{app} admin console', [inventory.infra['jump']], [groups['app']], [admin], scope=rule_scope,
                 rate=600 if position == 2 else None, quiet_until=n - 2, late=late)
        if position == 3:
            rule(path, f'{app} v1 replication', [inventory.retired], [groups['db']], [SERVICES + 'SVC-POSTGRESQL'],
                 scope=rule_scope, disabled=True, late=late)
        rule(path, f'{app} deny other inbound', ['ANY'], [app_group], action='DROP', scope=rule_scope,
             rate=number('rate-deny', app, low=10, high=900), late=late)
    if p.special_groups:
        phase2 = p.apps[-2][0]
        policy('app-' + phase2 + '-phase2', f'APP-{phase2.upper()}-PHASE2', 'Application', 900)
    default = policy('default-layer3-section', 'Default Layer3 Section', 'Application', 999999, system=True, late=900)
    rule(default, 'Default Layer3 Rule', ['ANY'], ['ANY'], action='ALLOW', rate=120000, system=True, late=900)


def _other_resources(inventory):
    p = inventory.profile
    gateway = f'/infra/domains/default/gateway-policies/edge-{p.prefix}-partners'
    inventory.other_resources = [
        {'resource_type': 'GatewayPolicy', 'id': f'edge-{p.prefix}-partners', 'display_name': f'Edge {p.code} partner access',
         'path': gateway, 'category': 'LocalGatewayRules', 'sequence_number': 10},
        {'resource_type': 'Rule', 'id': 'partner-https', 'display_name': 'Partner HTTPS', 'path': gateway + '/rules/partner-https',
         'rule_id': 50000 + p.net, 'source_groups': [inventory.ipsets['partners']], 'destination_groups': [inventory.all_web],
         'services': [SERVICES + 'HTTPS'], 'scope': [f'/infra/tier-1s/t1-{p.prefix}-workloads'], 'action': 'ALLOW'},
        {'resource_type': 'Tier1', 'id': f't1-{p.prefix}-workloads', 'display_name': f't1-{p.prefix}-workloads',
         'path': f'/infra/tier-1s/t1-{p.prefix}-workloads', 'tags': [{'scope': 'env', 'tag': p.env_tag}]},
    ]
