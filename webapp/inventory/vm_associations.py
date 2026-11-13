"""Collect compact, authoritative VM/group edges once, through the collection client.

These observations describe visible Policy membership at the default enforcement
point. They never establish that a group is empty or that a rule is effective.
"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import re
import threading
import time

LOG = logging.getLogger('nsx_inventory')
GROUP_PATH = re.compile(r'/(?:infra|global-infra|orgs/[^/]+/projects/[^/]+/infra)/domains/[^/]+/groups/[^/]+$')
SCOPE = re.compile(r'/(?:infra|global-infra|orgs/[^/]+/projects/[^/]+/infra)$')


def external_id(value, *, allow_id=False):
    if not isinstance(value, dict):
        raise ValueError('Saved VM identity is unavailable.')
    ids = {value['external_id']} if isinstance(value.get('external_id'), str) and value['external_id'] else set()
    compute_ids = value.get('compute_ids')
    if isinstance(compute_ids, list):
        ids.update(item[len('externalId:'):] for item in compute_ids if isinstance(item, str)
                   and item.startswith('externalId:') and item[len('externalId:'):])
    if len(ids) == 1:
        return next(iter(ids))
    if not ids and allow_id and isinstance(value.get('id'), str) and value['id']:
        return value['id']
    raise ValueError('A unique VM external ID is required; names and IPs cannot identify membership.')


def scope_for(vm):
    path = vm.get('path') or ''
    if not isinstance(path, str):
        raise ValueError('Invalid VM inventory path.')
    scope = path.split('/realized-state/', 1)[0] if '/realized-state/' in path else '/infra'
    if not SCOPE.fullmatch(scope):
        raise ValueError('Unsupported VM inventory scope.')
    return scope


def read(client, vm, *, testing=False, max_pages=100):
    """Normally one request per VM, with validated pagination and partial positives."""
    from .collector import AuditError
    from .request_pacing import CollectionDeadlineExceeded
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'unavailable',
              'items': [], 'notes': [], 'reported_count': None, 'requests': 0}
    rows, cursors = {}, set()
    try:
        params = {'vm_external_id': external_id(vm), 'page_size': 1 if testing else 1000}
        path = scope_for(vm) + '/virtual-machine-group-associations'
        for _ in range(1 if testing else max_pages):
            result['requests'] += 1
            page = client.get(path, params)
            if not isinstance(page, dict) or not isinstance(page.get('results'), list):
                raise ValueError('Missing VM association results array.')
            count = page.get('result_count')
            if count is not None:
                if type(count) is not int or count < 0:
                    raise ValueError('Invalid VM association total.')
                if result['reported_count'] is not None and result['reported_count'] != count:
                    result['notes'].append('VM association total changed during pagination.')
                if result['reported_count'] is None:
                    result['reported_count'] = count
            for item in page['results']:
                if (not isinstance(item, dict) or not isinstance(item.get('path'), str)
                        or not GROUP_PATH.fullmatch(item['path']) or item.get('is_valid') in (False, 'false')
                        or item.get('marked_for_delete')):
                    raise ValueError('Invalid VM group association.')
                key = item['path']
                if key in rows:
                    result['notes'].append('Duplicate VM group association during pagination.')
                name = item.get('target_display_name')
                rows[key] = {'path': key, 'name': name if isinstance(name, str) and name else key.rsplit('/', 1)[-1]}
            cursor = page.get('cursor')
            if testing:
                result['notes'].append('Testing sample; VM associations are incomplete.')
                break
            if not cursor:
                if result['reported_count'] is not None and result['reported_count'] != len(rows):
                    result['notes'].append('Returned associations do not match the reported total.')
                if result['reported_count'] is None and len(page['results']) >= params['page_size']:
                    result['notes'].append('Full association page without total or continuation.')
                result['status'] = 'partial' if result['notes'] else 'complete'
                break
            if not isinstance(cursor, str) or cursor in cursors:
                raise ValueError('Invalid or repeated VM association cursor.')
            cursors.add(cursor)
            params = dict(params, cursor=cursor)
        else:
            result['notes'].append('VM association page limit reached.')
    except CollectionDeadlineExceeded:
        raise
    except (AuditError, TimeoutError, ValueError) as exc:
        code = getattr(exc, 'status_code', None)
        if code:
            result['http_status'] = code
        result['notes'].append(f'VM association read incomplete (HTTP {code}).' if code
                               else 'VM association identity or response could not be verified.')
    result['items'] = sorted(rows.values(), key=lambda row: (row['name'].casefold(), row['path']))
    if result['status'] != 'complete' and (rows or testing):
        result['status'] = 'partial'
    result['notes'] = list(dict.fromkeys(result['notes']))
    return result


def collect(client, vms, *, workers=4, testing=False):
    """No tag inference, per-group expansion, per-edge HTTP, or live-cache writes."""
    started, lock = time.monotonic(), threading.Lock()
    metadata = {'version': 1, 'method': 'vm_associations', 'testing': testing,
                'scope': 'Visible Policy VM associations at the default enforcement point', 'vms': {}}
    identities = defaultdict(list)
    candidates = []
    for vm in vms:
        if not isinstance(vm, dict) or vm.get('marked_for_delete'):
            continue
        key = vm.get('path') or vm.get('external_id') or vm.get('id')
        if not isinstance(key, str) or not key:
            continue
        try:
            identities[(scope_for(vm), external_id(vm))].append(key)
        except ValueError:
            pass
        candidates.append((key, vm))
    ambiguous = {key for keys in identities.values() if len(keys) > 1 for key in keys}
    if testing:
        candidates = candidates[:1]
    stats = {'vms': len(candidates), 'completed': 0, 'requests': 0, 'statuses': {}, 'associations': 0}
    last_log, disabled = [started], {}

    def check(candidate):
        key, vm = candidate
        try:
            scope = scope_for(vm)
        except ValueError:
            scope = None
        if key in ambiguous or scope in disabled:
            result = {'status': 'unavailable', 'items': [], 'requests': 0, 'checked_at': None,
                      'notes': ['VM identity is ambiguous.' if key in ambiguous else 'VM association API denied or unsupported for this scope.']}
        else:
            result = read(client, vm, testing=testing)
            # A permission/unsupported response must not cause thousands of retries.
            if result.get('http_status') in (401, 403, 405, 501):
                with lock:
                    disabled[scope] = True
        with lock:
            metadata['vms'][key] = result
            stats['completed'] += 1
            stats['requests'] += result['requests']
            stats['associations'] += len(result['items'])
            states = stats['statuses']
            states[result['status']] = states.get(result['status'], 0) + 1
            now = time.monotonic()
            if now-last_log[0] >= 30 or stats['completed'] == len(candidates):
                LOG.info('VM association progress: completed=%d total=%d requests=%d statuses=%s elapsed_seconds=%.1f',
                         stats['completed'], len(candidates), stats['requests'], states, now-started)
                last_log[0] = now
            from .collector import report_progress
            report_progress(client, 'VM associations', stats['completed'], len(candidates))

    # Verify one identity per scope before parallel fan-out, avoiding a denial storm.
    first, rest, scopes = [], [], set()
    for candidate in candidates:
        try:
            if candidate[0] in ambiguous:
                raise ValueError('Ambiguous VM identity.')
            external_id(candidate[1])
            scope = scope_for(candidate[1])
        except ValueError:
            rest.append(candidate)
            continue
        if scope not in scopes:
            scopes.add(scope); first.append(candidate)
        else:
            rest.append(candidate)
    for candidate in first:
        check(candidate)
    with ThreadPoolExecutor(max_workers=1 if testing else workers) as pool:
        list(pool.map(check, rest))
    stats['seconds'] = round(time.monotonic()-started, 3)
    if stats['statuses'].get('complete', 0) != stats['vms']:
        LOG.warning('Saved VM associations are incomplete: statuses=%s total=%d; missing memberships remain unknown.',
                    stats['statuses'], stats['vms'])
    return metadata, stats


def apply_to_groups(tags, groups, metadata):
    """Build the inverse from the same saved edges, including excluded groups.

    Complete means complete within the collected, visible VM inventory. Missing
    identities/RBAC/failed lookups prevent a complete inverse; no negative result
    here is fed into cleanup eligibility.
    """
    members = defaultdict(dict)
    observations = metadata['vms']
    complete = (tags.get('vm_inventory_complete') is True and not metadata.get('testing')
                and all(record['status'] == 'complete' for record in observations.values()))
    for vm in tags.get('virtual_machines', []):
        key = vm.get('path') or vm.get('external_id') or vm.get('id')
        if key not in observations:
            complete = False
            continue
        record = observations[key]
        for group in record['items']:
            members[group['path']][key] = {'identity': key, 'vm_path': key,
                'name': vm.get('display_name') or key,
                **{field: vm[field] for field in ('path', 'id', 'external_id', 'compute_ids') if field in vm}}
    stamp = datetime.now(timezone.utc).isoformat()
    for group in groups:
        evidence = group.setdefault('resolved_membership', {'version': 1, 'endpoints': {}})
        evidence['checked_at'] = stamp
        evidence['endpoints']['virtual-machines'] = {
            'status': 'complete' if complete else 'partial',
            'items': sorted(members[group['path']].values(), key=lambda row: (row['name'].casefold(), row['identity'])),
            'reported_count': None, 'notes': [
                'VM members from direct associations for the collected visible VM inventory; default enforcement point.',
                'NSX reads are not atomic. Membership changes during collection can affect this list.'] +
                ([] if complete else ['Some VM identities or association lookups are incomplete; missing members remain unknown.'])}
    metadata['inverse_status'] = 'complete' if complete else 'partial'
