"""Saved member identities for browsing; never used to approve cleanup actions."""
from collections import defaultdict
from datetime import datetime, timezone

ENDPOINTS = ('virtual-machines', 'ip-addresses', 'logical-ports', 'logical-switches')
PAGE_SIZE = 1000
MEMBER_LIMIT = 10000


def member_record(value, endpoint):
    if isinstance(value, str) and endpoint == 'ip-addresses' and value:
        return {'identity': value, 'name': value}
    if not isinstance(value, dict):
        raise ValueError('Malformed member identity')
    fields = ('external_id', 'id', 'path', 'display_name', 'ip_address', 'ip_addresses',
              'realization_specific_identifier', 'compute_ids')
    row = {key: value[key] for key in fields if key in value}
    identity = next((row[key] for key in ('external_id', 'path', 'id', 'realization_specific_identifier', 'ip_address')
                     if isinstance(row.get(key), str) and row[key]), None)
    if not identity:
        raise ValueError('Member has no stable identity')
    name = row.get('display_name')
    return dict(row, identity=identity, name=name if isinstance(name, str) and name else identity)


def collect(client, group, *, probe=None, testing=False, limit=MEMBER_LIMIT):
    # Import here so the offline collector remains usable without Django.
    from .collector import AuditError
    evidence = {'version': 1, 'checked_at': datetime.now(timezone.utc).isoformat(), 'endpoints': {}}
    for endpoint in ENDPOINTS:
        result = {'status': 'unavailable', 'items': [], 'reported_count': None, 'notes': []}
        evidence['endpoints'][endpoint] = result
        seen, cursors, expected, raw_count = {}, set(), None, 0
        cached = (probe.pages.get(endpoint) if probe else None)
        # A complete tiny probe can be reused; a positive sample is not a list.
        reuse = (isinstance(cached, dict) and type(cached.get('result_count')) is int
                 and isinstance(cached.get('results'), list)
                 and cached['result_count'] == len(cached.get('results', [])) <= 1
                 and not cached.get('cursor'))
        params = {'page_size': 1 if testing else PAGE_SIZE}
        try:
            failure = probe.failures.get(endpoint) if probe else None
            if isinstance(failure, AuditError) and failure.status_code in (400, 401, 403, 404, 405, 501):
                raise failure
            for _ in range(1 if testing else 100):
                page = cached if reuse else client.get(group['path'] + '/members/' + endpoint, params)
                reuse = False
                if not isinstance(page, dict) or not isinstance(page.get('results'), list):
                    raise ValueError('Missing member results array')
                count = page.get('result_count')
                if count is not None:
                    if type(count) is not int or count < 0:
                        raise ValueError('Invalid member result_count')
                    if expected is not None and expected != count:
                        result['notes'].append('Reported member total changed during pagination.')
                    expected = count if expected is None else expected
                result['reported_count'] = expected
                values = page['results']
                raw_count += len(values)
                for value in values:
                    row = member_record(value, endpoint)
                    identity = row['identity']
                    if identity in seen:
                        result['notes'].append('Duplicate member identity returned during pagination.')
                    elif len(seen) < limit:
                        seen[identity] = row
                cursor = page.get('cursor')
                if testing:
                    result['notes'].append('Testing sample; member list is incomplete.')
                    break
                if len(seen) >= limit and (cursor or raw_count > limit):
                    result['notes'].append('Saved member limit reached; additional members may exist.')
                    break
                if not cursor:
                    if expected is not None and expected != len(seen):
                        result['notes'].append('Returned identities do not match the reported member total.')
                    elif expected is None and len(values) >= params['page_size']:
                        result['notes'].append('Full page without a total or continuation; completeness is unknown.')
                    result['status'] = 'partial' if result['notes'] else 'complete'
                    break
                if not isinstance(cursor, str) or cursor in cursors:
                    raise ValueError('Repeated or invalid member pagination cursor')
                cursors.add(cursor)
                params = dict(params, cursor=cursor)
            else:
                result['notes'].append('Member page limit reached.')
        except (AuditError, ValueError) as exc:
            code = getattr(exc, 'status_code', None)
            result['notes'].append('Membership read failed' + (f' (HTTP {code}).' if code else ': ' + str(exc)))
            if not seen and code in (405, 501):
                result['status'] = 'not_supported'
        result['items'] = sorted(seen.values(), key=lambda row: (row['name'].casefold(), row['identity']))
        if result['status'] == 'unavailable' and (seen or result['notes'] and testing):
            result['status'] = 'partial'
        result['notes'] = list(dict.fromkeys(result['notes']))
    return evidence


def vm_identities(row):
    values = {row[key] for key in ('path', 'external_id', 'id', 'realization_specific_identifier')
              if isinstance(row.get(key), str) and row[key]}
    # NSX realized VMs can retain the fabric external ID only in compute_ids.
    # Other compute IDs (host-local IDs, bios/location IDs) are not interchangeable.
    compute_ids = row.get('compute_ids')
    if isinstance(compute_ids, list):
        values.update(value[len('externalId:'):] for value in compute_ids
                      if isinstance(value, str) and value.startswith('externalId:') and value[len('externalId:'):])
    return values


def vm_links(tags, groups, metadata):
    """Invert only exact, unambiguous saved VM identities; never match by name/tag/IP."""
    if metadata.get('version') != 1:
        return {}, 'not_collected'
    aliases = defaultdict(set)
    names = {}
    for vm in tags.get('virtual_machines', []):
        path = vm.get('path') or vm.get('external_id') or vm.get('id')
        if not path:
            continue
        names[path] = vm.get('display_name') or path
        for identity in vm_identities(vm):
            aliases[identity].add(path)
    links = defaultdict(dict)
    states = []
    ambiguous = any(len(paths) > 1 for paths in aliases.values())
    unlinked = False
    for group in groups:
        endpoint = group.get('resolved_membership', {}).get('endpoints', {}).get('virtual-machines', {})
        states.append(endpoint.get('status', 'not_collected'))
        for member in endpoint.get('items', []):
            matches = set().union(*(aliases.get(identity, set()) for identity in vm_identities(member)))
            member.pop('vm_path', None)
            if len(matches) == 1:
                path = next(iter(matches))
                member['vm_path'] = path
                member['name'] = names[path]
                links[path][group['path']] = {'path': group['path'], 'name': group['name'],
                                             'status': endpoint.get('status', 'partial')}
            else:
                unlinked = True
    if states and not any(state in ('complete', 'partial') for state in states):
        status = 'unavailable'
    else:
        status = ('complete' if all(state == 'complete' for state in states)
                  and not metadata.get('excluded_group_count') and tags.get('vm_inventory_complete') is True
                  and not metadata.get('testing') and not ambiguous and not unlinked else 'partial')
    return links, status


def vm_summary(row):
    """Legacy tag-reference counts must never appear as resolved membership counts."""
    result = dict(row)
    if result.get('membership_evidence_version') != 1:
        result.update(resolved_group_count=None, resolved_membership_status='not_collected')
    return result
