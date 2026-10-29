"""Read-only naming evaluation. No NSX/vCenter client or mutation capability."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from django.db.models import Q
from .models import NamingPolicy, NamingScopeBinding, NamingProposal, SnapshotRecord


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def baseline(vm):
    return digest([vm['identity'], vm['name'], sorted((t.get('scope', ''), t.get('tag', '')) for t in vm['tags'])])


def inventory(environment):
    snapshot = environment.snapshots.filter(testing=False, imported=False).only('pk', 'generated_at', 'summary', 'browsing_status').first()
    if not snapshot:
        return None, [], 'No full collection is available. Collect this environment before evaluating names.'
    if snapshot.browsing_status != 'ready':
        return snapshot, [], 'Snapshot browsing data is archived or being prepared. Restore its browsing data first.'
    rows = SnapshotRecord.objects.filter(snapshot=snapshot, view='vms').order_by('ordinal').values('pk', 'name', 'data__path', 'data__tags')
    vms = [{'id': r['pk'], 'identity': r['data__path'] or '', 'name': r['name'], 'tags': r['data__tags'] or []} for r in rows.iterator(chunk_size=500)]
    warning = 'Inventory coverage has warnings; results are provisional and cannot establish compliance.' if snapshot.summary.get('inventory_warnings') else ''
    if not vms:
        warning = 'No indexed VMs were found in this snapshot. Check collection coverage and browsing preparation.'
    return snapshot, vms, warning


def tag_values(tags):
    values = defaultdict(set)
    for tag in tags:
        values[tag.get('scope', '')].add(str(tag.get('tag', '')))
    return values


def scope(component, bindings):
    return bindings.get(component['field'], component.get('scope', ''))


def evaluate(vm, policies, bindings):
    values = tag_values(vm['tags'])
    candidates = []
    for policy in policies:
        config = policy.configuration
        match_field = config.get('match_field')
        if match_field:
            component = next((c for c in config['components'] if c['field'] == match_field), None)
            if not component or config.get('match_value') not in values[scope(component, bindings.get(policy.pk, {}))]:
                continue
        candidates.append(policy)
    # Explicit environment overrides precede global rules; category rules precede defaults.
    local = [p for p in candidates if p.environment_id]
    candidates = local or candidates
    specific = [p for p in candidates if p.configuration.get('match_field')]
    candidates = specific or candidates
    row = dict(vm, proposed='', policy=None, status='No applicable policy', reasons=[], components=[])
    if not candidates:
        return row
    if len(candidates) != 1:
        row.update(status='Ambiguous policy', reasons=['Multiple matching policies: ' + ', '.join(p.name for p in candidates)])
        return row
    policy = candidates[0]
    row['policy'] = policy
    config = policy.configuration
    parts, issues = [], []
    for component in config['components']:
        source = component['source']
        actual_scope = scope(component, bindings.get(policy.pk, {}))
        raw = [component.get('literal', '')] if source == 'fixed' else [vm['identity'].rsplit('/', 1)[-1]] if source == 'identity' else sorted(values[actual_scope])
        reason = ''
        output = ''
        if len(raw) > 1:
            reason = 'Ambiguous tags'
        elif not raw or not raw[0]:
            if component['required']:
                reason = 'Missing required tags'
        else:
            output = raw[0]
            mapping = component.get('mapping', {})
            if mapping:
                if output not in mapping:
                    reason = 'Invalid value'
                else:
                    output = mapping[output]
            if component['case'] == 'upper': output = output.upper()
            if component['case'] == 'lower': output = output.lower()
            pattern = {'alphanumeric': r'[A-Za-z0-9]+', 'digits': r'[0-9]+', 'extended': r'[A-Za-z0-9_.-]+'}[component['format']]
            if not re.fullmatch(pattern, output) or len(output) > component['max_length']:
                reason = 'Invalid value'
        row['components'].append({'field': component['field'], 'scope': actual_scope if source == 'tag' else source, 'observed': ', '.join(raw) or 'Missing', 'output': output, 'issue': reason})
        if reason:
            issues.append(reason)
            row['reasons'].append(component['field'] + ': ' + reason)
        if output:
            parts.append(output)
    if issues:
        row['status'] = next(label for label in ['Ambiguous tags', 'Missing required tags', 'Invalid value'] if label in issues)
        return row
    proposed = config['separator'].join(parts)
    if not proposed or len(proposed) > config['max_length']:
        row.update(status='Invalid value', reasons=['Result is empty or exceeds the maximum name length.'])
        return row
    row.update(proposed=proposed, status='Compliant' if proposed == vm['name'] else 'Name differs')
    return row


def assess(environment, replacement=None, simulation=False):
    snapshot, vms, warning = inventory(environment)
    policies = list(NamingPolicy.objects.filter(enabled=True).filter(Q(environment__isnull=True) | Q(environment=environment)))
    if replacement:
        policies = [p for p in policies if p.pk != replacement.pk]
        if replacement.environment_id in (None, environment.pk): policies.append(replacement)
    bindings = {b.policy_id: (b.draft_scopes if replacement and b.policy_id == replacement.pk else b.scopes) for b in NamingScopeBinding.objects.filter(environment=environment)}
    proposals = {p.vm_identity: p for p in NamingProposal.objects.filter(environment=environment).select_related('owner')}
    rows = []
    for vm in vms:
        proposal = proposals.get(vm['identity'])
        stale = bool(proposal and proposal.baseline != baseline(vm))
        effective = vm
        if simulation and proposal and not stale and proposal.state != 'rejected':
            effective = dict(vm, tags=[t for t in vm['tags'] if t.get('scope') not in proposal.tags] + [{'scope': k, 'tag': v} for k, v in proposal.tags.items()])
        row = evaluate(effective, policies, bindings)
        row.update(proposal=proposal, stale=stale, observed_tags=vm['tags'], baseline=baseline(vm))
        rows.append(row)
    names = defaultdict(set)
    for vm in vms: names[vm['name'].casefold()].add(vm['identity'])
    for row in rows:
        if row['proposed']: names[row['proposed'].casefold()].add(row['identity'])
    for row in rows:
        if row['proposed'] and len(names[row['proposed'].casefold()]) > 1:
            row['status'] = 'Duplicate proposed name'
            row['reasons'].append('This name matches another VM’s current or proposed name in this environment (case-insensitive).')
        if warning and row['status'] == 'Compliant':
            row['status'] = 'Evidence incomplete'
            row['reasons'].append(warning)
    return snapshot, rows, warning


def counts(rows):
    return sorted(Counter(row['status'] for row in rows).items())
