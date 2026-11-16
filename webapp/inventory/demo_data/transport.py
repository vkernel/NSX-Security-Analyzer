"""Serve a synthetic inventory to the real collector without any network I/O.

``DemoNSXClient`` is the collector's own ``NSXClient`` (paths, retries, pagination
checks and error handling are unchanged) whose connection pool is replaced by an
in-process responder. ``no_network`` additionally makes any accidental socket use
fail, so a demonstration load can never reach a real NSX Manager.
"""
from contextlib import contextmanager
import json
import socket
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

from .. import collector
from .scenario import DOMAIN, ENFORCEMENT_POINT, GROUPS, POLICIES, SEGMENTS

MEMBER_ENDPOINTS = {'virtual-machines', 'ip-addresses', 'logical-ports', 'segment-ports',
                    'logical-switches', 'segments', 'vifs'}


class NetworkBlocked(OSError):
    pass


@contextmanager
def no_network():
    """Refuse Python-level DNS lookups and socket connections while active.

    Only name resolution of the configured database hosts is allowed: psycopg
    resolves them in Python before libpq opens the PostgreSQL connection.
    """
    from django.conf import settings
    databases = {str(options.get('HOST') or '') for options in settings.DATABASES.values()} - {''}
    saved = socket.getaddrinfo, socket.create_connection, socket.socket.connect, socket.socket.connect_ex
    original_getaddrinfo = saved[0]

    def refuse(*args, **kwargs):
        raise NetworkBlocked('Network access is disabled while demonstration data is generated.')

    def resolve(host, *args, **kwargs):
        if isinstance(host, bytes):
            host = host.decode()
        if host in databases:
            return original_getaddrinfo(host, *args, **kwargs)
        return refuse()
    socket.getaddrinfo = resolve
    socket.create_connection = refuse
    socket.socket.connect = socket.socket.connect_ex = refuse
    try:
        yield
    finally:
        socket.getaddrinfo, socket.create_connection, socket.socket.connect, socket.socket.connect_ex = saved


def response(status, payload, reason='OK'):
    return SimpleNamespace(status=status, reason=reason, data=json.dumps(payload).encode(), headers={})


def page(results, params, total=None):
    """NSX-style cursor pagination; the cursor is an opaque offset string."""
    start = int(params.get('cursor', '0') or 0)
    size = int(params['page_size']) if 'page_size' in params else None
    chunk = results[start:] if size is None else results[start:start + size]
    body = {'results': chunk, 'result_count': len(results) if total is None else total}
    if size is not None and start + size < len(results):
        body['cursor'] = str(start + size)
    return body


class SyntheticManager:
    """Read-only Policy/Manager API responses for one synthetic inventory."""

    def __init__(self, inventory, mode='normal'):
        self.inventory = inventory
        self.mode = mode
        self.requests = 0

    def search_resources(self):
        inv = self.inventory
        rules = [rule for items in inv.rules.values() for rule in items]
        resources = inv.groups + inv.services + inv.policies + rules + inv.segments + inv.ports + inv.other_resources
        return sorted(resources, key=lambda item: item['path'])

    def handle(self, path, params):
        self.requests += 1
        if self.mode == 'unauthorized':
            return 403, {'error_code': 403, 'error_message': 'The credentials were incorrect or the account '
                         'specified has been locked.', 'module_name': 'common-services'}
        if self.mode == 'unavailable':
            return 503, {'error_code': 503, 'error_message': 'Some appliance components are not functioning '
                         'properly.', 'module_name': 'common-services'}
        inv = self.inventory
        if path == '/infra/domains':
            return 200, page([{'resource_type': 'Domain', 'id': 'default', 'display_name': 'default',
                               'path': DOMAIN}], params)
        if path == DOMAIN + '/groups':
            return 200, page(inv.groups, params)
        if path == '/infra/services':
            return 200, page(inv.services, params)
        if path == '/search/query':
            return 200, page(self.search_resources(), params)
        if path == DOMAIN + '/security-policies':
            return 200, page(inv.policies, params)
        if path == '/infra/tags':
            return 200, page(inv.tags, params)
        if path == '/infra/realized-state/virtual-machines':
            vms = sorted(inv.vms, key=lambda vm: vm['external_id'])
            # The partial collection observes a VM count that changes while it reads.
            return 200, page(vms, params, total=len(vms) + 2 if inv.partial else None)
        if path == '/infra/virtual-machine-group-associations':
            groups = sorted(inv.associations.get(params.get('vm_external_id'), []), key=lambda g: g['path'])
            return 200, page([{'resource_type': 'PolicyResourceReference', 'path': g['path'],
                               'target_display_name': g['display_name'], 'is_valid': True} for g in groups], params)
        if path == '/api/v1/fabric/vifs':
            vifs = sorted(inv.vifs, key=lambda vif: vif['external_id'])
            return 200, page(vifs, params, total=len(vifs) + 3 if inv.partial else None)
        if path == '/api/v1/fabric/virtual-machines':
            return 200, page([{'resource_type': 'VirtualMachine', 'external_id': vm['external_id'],
                               'display_name': vm['display_name']} for vm in inv.vms], params)
        if path.startswith(POLICIES):
            return self.firewall(path[len(POLICIES):], params)
        if path.startswith(GROUPS) and '/members/' in path:
            group_path, endpoint = path.split('/members/', 1)
            return self.members(group_path, endpoint, params)
        if path.startswith(SEGMENTS) and path.endswith('/ports'):
            return 200, page(sorted(inv.ports_by_segment.get(path[:-len('/ports')], []),
                                    key=lambda port: port['path']), params)
        return 404, {'error_code': 600, 'error_message': 'The requested object could not be found.'}

    def firewall(self, remainder, params):
        inv = self.inventory
        parts = remainder.split('/')
        policy_path = POLICIES + parts[0]
        if policy_path not in inv.rules:
            return 404, {'error_code': 600, 'error_message': 'The requested object could not be found.'}
        rules = sorted(inv.rules[policy_path], key=lambda rule: rule['sequence_number'])
        if parts[1:] == ['rules']:
            return 200, page(rules, params)
        unsupported = {'error_code': 500209, 'module_name': 'firewall',
                       'error_message': 'Statistics are not supported for Ethernet (Layer-2) rules.'}
        if parts[1:] == ['statistics']:
            if policy_path in inv.unsupported_statistics:
                return 400, unsupported
            counters = [inv.counters[rule['path']] for rule in rules]
            return 200, {'results': [{'enforcement_point': ENFORCEMENT_POINT, 'statistics': {
                'results': counters, 'result_count': len(counters)}}], 'result_count': 1}
        if len(parts) == 4 and parts[1] == 'rules' and parts[3] == 'statistics':
            if policy_path in inv.unsupported_statistics:
                return 400, unsupported
            rule_path = policy_path + '/rules/' + parts[2]
            if rule_path not in inv.counters:
                return 404, {'error_code': 600, 'error_message': 'The requested object could not be found.'}
            return 200, {'results': [{'enforcement_point': ENFORCEMENT_POINT,
                                      'statistics': inv.counters[rule_path]}], 'result_count': 1}
        return 404, {'error_code': 600, 'error_message': 'The requested object could not be found.'}

    def members(self, group_path, endpoint, params):
        inv = self.inventory
        if group_path not in inv.group_by_path or endpoint not in MEMBER_ENDPOINTS:
            return 404, {'error_code': 600, 'error_message': 'The requested object could not be found.'}
        if group_path in inv.unreadable_groups:
            return 500, {'error_code': 100, 'module_name': 'policy',
                         'error_message': 'General error has occurred while evaluating group members.'}
        if endpoint == 'virtual-machines':
            rows = [{'resource_type': 'VirtualMachine', 'external_id': ext, 'id': ext,
                     'display_name': inv.vm_by_external[ext]['display_name']}
                    for ext in sorted(inv.vm_members(group_path))]
        elif endpoint == 'ip-addresses':
            rows = inv.ip_members(group_path)
        elif endpoint in ('logical-ports', 'segment-ports'):
            rows = [{key: port[key] for key in ('resource_type', 'id', 'display_name', 'path', 'parent_path')}
                    for port in inv.port_members(group_path)]
        elif endpoint in ('logical-switches', 'segments'):
            segments = {segment['path']: segment for segment in inv.segments}
            rows = [{key: segments[path][key] for key in ('resource_type', 'id', 'display_name', 'path')}
                    for path in sorted(inv.segment_paths(group_path)) if path in segments]
        else:
            rows = []
        return 200, page(rows, params)


class FakePool:
    """Stands in for ``urllib3.PoolManager``; accepts only the configured manager."""

    def __init__(self, manager, host):
        self.manager = manager
        self.host = host

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        if method != 'GET' or parsed.scheme != 'https' or parsed.hostname != self.host:
            raise collector.urllib3.exceptions.HTTPError('Synthetic manager accepts read-only requests only.')
        path = unquote(parsed.path)
        if path.startswith('/policy/api/v1'):
            path = path[len('/policy/api/v1'):]
        params = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
        status, payload = self.manager.handle(path, params)
        reasons = {200: 'OK', 400: 'Bad Request', 403: 'Forbidden', 404: 'Not Found', 500: 'Internal Server Error',
                   503: 'Service Unavailable'}
        return response(status, payload, reasons.get(status, 'Error'))

    def clear(self):
        pass


class DemoNSXClient(collector.NSXClient):
    """The collector's client with an in-process synthetic manager as its pool."""

    def __init__(self, manager_url, username, password, synthetic, **options):
        super().__init__(manager_url, username, password, **options)
        self.http.clear()
        self.http = FakePool(synthetic, urlsplit(self.base_url).hostname)
