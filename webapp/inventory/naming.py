"""Read-only naming evaluation. No NSX/vCenter client or mutation capability."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from django.db.models import Q
from .models import NamingPolicy, NamingScopeBinding, NamingProposal, SnapshotRecord


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def baseline(obj):
    return digest([obj['identity'], obj['name'], obj.get('definition'), obj.get('unique_id'), obj['tags']])


def group_evidence(config):
    """Classify definitions, never resolved member counts. Complex logic stays explicit."""
    dynamic, static, nested, tags, members, notes = [], [], [], [], [], []
    complex_logic = False
    def visit(nodes):
        nonlocal complex_logic
        if not isinstance(nodes, list):
            notes.append('Malformed membership definition'); return
        for node in nodes:
            if not isinstance(node, dict):
                notes.append('Malformed membership expression'); continue
            kind = node.get('resource_type')
            if kind == 'Condition':
                dynamic.append(node)
                value = str(node.get('value', ''))
                if node.get('member_type') == 'VirtualMachine' and node.get('key') == 'Tag' and node.get('operator') == 'EQUALS' and '|' in value:
                    scope, tag = value.split('|', 1)
                    if scope and tag: tags.append({'scope': scope, 'tag': tag})
                    else: notes.append('Tag condition has no scope or value')
                else: notes.append('Condition cannot supply a positive VM tag value')
            elif kind == 'ConjunctionOperator':
                if node.get('conjunction_operator') != 'AND': complex_logic = True
            elif kind == 'NestedExpression':
                complex_logic = True
                visit(node.get('expressions', []))
            elif kind in ('IPAddressExpression', 'MACAddressExpression', 'ExternalIDExpression', 'PathExpression'):
                key = {'IPAddressExpression':'ip_addresses', 'MACAddressExpression':'mac_addresses', 'ExternalIDExpression':'external_ids', 'PathExpression':'paths'}[kind]
                static.append(node)
                for value in node.get(key, []):
                    members.append({'kind': kind, 'value': str(value), 'member_type': node.get('member_type', '')})
                    if kind == 'PathExpression' and '/groups/' in str(value): nested.append(str(value))
            else: notes.append('Unsupported expression: ' + str(kind))
    if not isinstance(config, dict) or 'expression' not in config:
        return dict(group_type='unknown', tags=[], candidate_tags=[], members=[], nested=[], evidence_notes=['Group definition unavailable'], needs_review=True, conditions=[])
    visit(config['expression'])
    if config.get('extended_expression'):
        notes.append('Extended expressions require review')
    kind = 'mixed' if dynamic and static or nested else 'dynamic' if dynamic else 'static'
    if not dynamic and not static and notes: kind = 'unknown'
    if complex_logic: notes.append('Alternative or nested condition logic requires review')
    if nested: notes.append('Nested group evidence is supporting evidence, not an automatic naming source')
    needs_review = bool(notes) or kind == 'mixed'
    return dict(group_type=kind, tags=tags if kind == 'dynamic' and not needs_review else [],
                candidate_tags=tags, conditions=[f"{n.get('member_type', '')} {n.get('key', '')} {n.get('operator', '')} {n.get('value', '')}" for n in dynamic], members=members, nested=nested, evidence_notes=notes, needs_review=needs_review)


def inventory(environment):
    snapshot = environment.snapshots.filter(testing=False, imported=False).only('pk', 'generated_at', 'summary', 'browsing_status').first()
    if not snapshot:
        return None, [], 'No full collection is available. Collect this environment before evaluating names.'
    if snapshot.browsing_status != 'ready':
        return snapshot, [], 'Snapshot browsing data is archived or being prepared. Restore its browsing data first.'
    records = SnapshotRecord.objects.filter(snapshot=snapshot, view='inventory', data__kind='group').order_by('ordinal').values('pk', 'name', 'data__path', 'data__configuration', 'data__unique_id')
    groups = []
    for r in records.iterator(chunk_size=500):
        definition = r['data__configuration']
        groups.append(dict(id=r['pk'], identity=r['data__path'] or '', name=r['name'], definition=definition,
                           unique_id=r['data__unique_id'], **group_evidence(definition)))
    by_path = {g['identity']: g for g in groups}
    # Project only explicit VM identities and tags, not large rule/member payloads.
    vm_tags = {}
    if any(m['kind'] == 'ExternalIDExpression' and m['member_type'] == 'VirtualMachine' for g in groups for m in g['members']):
        for row in SnapshotRecord.objects.filter(snapshot=snapshot, view='vms').values('data__path', 'data__vm_details__external_id', 'data__tags').iterator(chunk_size=500):
            for key in (row['data__path'], row['data__vm_details__external_id']):
                if key: vm_tags[key] = row['data__tags'] or []
    for group in groups:
        group['supporting'] = []
        expanded = set()
        def inherit(path, seen):
            if len(expanded) >= 100 or len(seen) >= 30:
                group['supporting'].append({'source': path, 'detail': 'Nested evidence limit reached; review source inventory', 'tags': []}); return
            if path in seen:
                group['supporting'].append({'source': path, 'detail': 'Cycle detected; review required', 'tags': []}); return
            if path in expanded: return
            expanded.add(path)
            child = by_path.get(path)
            group['supporting'].append({'source': path, 'detail': 'Nested definition' if child else 'Nested definition unavailable', 'tags': child['candidate_tags'] if child else []})
            if child:
                for nested in child['nested']: inherit(nested, seen | {path})
        for path in group['nested']: inherit(path, {group['identity']})
        for member in group['members']:
            if member['kind'] == 'ExternalIDExpression' and member['member_type'] == 'VirtualMachine':
                tags = vm_tags.get(member['value'])
                group['supporting'].append({'source': member['value'], 'detail': 'Member VM tags: suggestion only' if tags is not None else 'Member VM evidence unavailable', 'tags': tags or []})
    warning = 'Inventory coverage has warnings; results are provisional and cannot establish compliance.' if snapshot.summary.get('inventory_warnings') else ''
    if not groups: warning = 'No indexed groups found. Check collection coverage and browsing preparation.'
    return snapshot, groups, warning


def tag_values(tags):
    values = defaultdict(set)
    for tag in tags:
        values[tag.get('scope', '')].add(str(tag.get('tag', '')))
    return values


def scope(component, bindings):
    return bindings.get(component['field'], component.get('scope', ''))


def evaluate(group, policies, bindings):
    values = tag_values(group['tags'])
    candidates = []
    for policy in policies:
        config = policy.configuration
        if config.get('target') != 'group' or config.get('group_type', 'all') not in ('all', group.get('group_type')):
            continue
        match_field = config.get('match_field')
        if match_field:
            component = next((c for c in config['components'] if c['field'] == match_field), None)
            if not component or config.get('match_value') not in values[scope(component, bindings.get(policy.pk, {}))]:
                continue
        candidates.append(policy)
    # Explicit environment overrides precede global rules; category rules precede defaults.
    local = [p for p in candidates if p.environment_id]
    candidates = local or candidates
    typed = [p for p in candidates if p.configuration.get('group_type', 'all') != 'all']
    candidates = typed or candidates
    specific = [p for p in candidates if p.configuration.get('match_field')]
    candidates = specific or candidates
    row = dict(group, proposed='', policy=None, status='No applicable policy', reasons=[], components=[])
    if not candidates:
        return row
    if len(candidates) != 1:
        row.update(status='Ambiguous policy', reasons=['Multiple matching policies: ' + ', '.join(p.name for p in candidates)])
        return row
    policy = candidates[0]
    row['policy'] = policy
    config = policy.configuration
    parts, issues = [], []
    if group.get('needs_review') and not group.get('reviewed_components'):
        row.update(status='Definition review required', reasons=group.get('evidence_notes', []) or ['Mixed definition requires reviewed component values.'])
        return row
    for component in config['components']:
        source = component['source']
        actual_scope = scope(component, bindings.get(policy.pk, {}))
        raw = [component.get('literal', '')] if source == 'fixed' else [group['identity'].rsplit('/', 1)[-1]] if source == 'identity' else [group.get('group_type', 'unknown')] if source == 'type' else sorted(values[actual_scope])
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
        row['components'].append({'field': component['field'], 'source': group.get('component_source') if source == 'tag' and actual_scope in group.get('proposed_scopes', set()) else 'Definition tag' if source == 'tag' else source, 'scope': actual_scope if source == 'tag' else source, 'observed': ', '.join(raw) or 'Missing', 'output': output, 'issue': reason})
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
    row.update(proposed=proposed, status='Compliant' if proposed == group['name'] else 'Name differs')
    return row


def assess(environment, replacement=None, simulation=False):
    snapshot, groups, warning = inventory(environment)
    policies = list(NamingPolicy.objects.filter(enabled=True, removed_at__isnull=True).filter(Q(environment__isnull=True) | Q(environment=environment)))
    if replacement:
        policies = [p for p in policies if p.pk != replacement.pk]
        if replacement.environment_id in (None, environment.pk): policies.append(replacement)
    bindings = {b.policy_id: (b.draft_scopes if replacement and b.policy_id == replacement.pk else b.scopes) for b in NamingScopeBinding.objects.filter(environment=environment)}
    proposals = {p.group_path: p for p in NamingProposal.objects.filter(environment=environment, object_type='group').select_related('owner', 'reviewed_by')}
    rows = []
    for group in groups:
        proposal = proposals.get(group['identity'])
        stale = bool(proposal and proposal.baseline != baseline(group))
        effective = group
        accepted = bool(proposal and not stale and proposal.state == 'reviewed')
        if proposal and not stale and (accepted or simulation and proposal.state == 'draft'):
            effective = dict(group, reviewed_components=accepted, proposed_scopes=set(proposal.tags), component_source='Reviewed value' if accepted else 'Draft simulation', tags=[t for t in group['tags'] if t.get('scope') not in proposal.tags] + [{'scope': k, 'tag': v} for k, v in proposal.tags.items()])
        row = evaluate(effective, policies, bindings)
        row.update(proposal=proposal, stale=stale, observed_tags=group['tags'], baseline=baseline(group))
        rows.append(row)
    names = defaultdict(set)
    for group in groups: names[group['name'].casefold()].add(group['identity'])
    for row in rows:
        if row['proposed']: names[row['proposed'].casefold()].add(row['identity'])
    for row in rows:
        if row['proposed'] and len(names[row['proposed'].casefold()]) > 1:
            row['status'] = 'Duplicate proposed name'
            row['reasons'].append('This name matches another group’s current or proposed name in this environment (case-insensitive).')
        if warning and row['status'] == 'Compliant':
            row['status'] = 'Evidence incomplete'
            row['reasons'].append(warning)
    policy_stamp = digest([[(p.pk, p.revision, p.configuration) for p in sorted(policies, key=lambda p: p.pk)], bindings])
    for row in rows:
        row['policy_stamp'] = policy_stamp
        proposal = row['proposal']
        row['decision_stamp'] = digest([row['baseline'], policy_stamp, [proposal.state, proposal.tags, proposal.note, str(proposal.updated_at)] if proposal else None])
        row['exception_stale'] = bool(proposal and proposal.state == 'exception' and (row['stale'] or proposal.exception_policy_stamp != policy_stamp))
        if proposal and proposal.state == 'exception' and not row['exception_stale']:
            row['reasons'].append('Reviewed exception: ' + proposal.note)
            row['status'] = 'Reviewed exception'
    return snapshot, rows, warning


def counts(rows):
    return sorted(Counter(row['status'] for row in rows).items())
