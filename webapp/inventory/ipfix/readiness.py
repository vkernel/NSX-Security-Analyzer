"""Read-only evidence; policy realization is never treated as proof of UDP delivery."""
from ..collector import AuditError


def inspect_readiness(client, plan):
    evidence = []
    paths = {p['path'] for p in plan['profiles']
             if plan.get('profile_mode') == 'new' or any(
                 op['path'] == p.get('ipfix_dfw_collector_profile_path') for op in plan['checks'])}
    paths.update(op['path'] for op in plan['operations']
                 if op['body'].get('resource_type') == 'IPFIXDFWProfile')
    for path in sorted(paths):
        try:
            page = client.get('/infra/realized-state/realized-entities', {'intent_path': path})
            records = page.get('results')
            if not isinstance(records, list):
                raise AuditError('Missing realization results')
            states = sorted({str(r.get('state', 'Unknown')) for r in records})
            alarms = sum(len(r.get('alarms') or []) for r in records)
            evidence.append({'check': path, 'result':
                ('Realization: ' + ', '.join(states) + f'; alarms: {alarms}') if records else
                'No realized entities returned. A proposed resource may not exist yet.'})
        except AuditError:
            evidence.append({'check': path, 'result': 'Realization unavailable; check connectivity and read permissions.'})
    group = plan.get('group')
    if group:
        for endpoint, label in [('logical-ports', 'Resolved logical ports'), ('logical-switches', 'Resolved logical switches')]:
            try:
                page = client.get(group + '/members/' + endpoint, {'page_size': 1})
                results = page.get('results')
                if not isinstance(results, list):
                    raise AuditError('Missing membership results')
                found = bool(results) or (type(page.get('result_count')) is int and page['result_count'] > 0)
                result = 'Members found; effective export still requires host validation.' if found else 'No members returned; eligibility is not established.'
            except AuditError:
                result = 'Membership check unavailable; eligibility is unknown.'
            evidence.append({'check': label, 'result': result})
        try:
            bindings = list(client.items(group + '/group-monitoring-profile-binding-maps'))
            profiles = sorted({b['ipfix_dfw_profile_path'] for b in bindings if b.get('ipfix_dfw_profile_path')})
            evidence.append({'check': 'Current group bindings', 'result': ', '.join(profiles) or 'No DFW IPFIX binding found for this group.'})
        except AuditError:
            evidence.append({'check': 'Current group bindings', 'result': 'Unavailable; existing bindings could not be verified.'})
    else:
        evidence.append({'check': 'Workload scope', 'result': 'No group selected. Existing/global scope is retained and has not been independently verified.'})
    for profile in plan['profiles']:
        evidence.append({'check': 'Existing profile priority', 'result': '{}: {}. Lowest number wins on overlapping ports; scope overlap is not calculated.'.format(profile['path'], profile.get('priority', 0))})
    return evidence
