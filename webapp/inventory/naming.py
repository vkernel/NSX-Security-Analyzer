from difflib import SequenceMatcher
from django.utils import timezone
"""Read-only naming evaluation. No NSX/vCenter client or mutation capability."""
import hashlib
import ipaddress
import json
import re
from collections import Counter, defaultdict
from django.db.models import Q, F
from .models import NamingPolicy, NamingScopeBinding, NamingProposal, SnapshotRecord


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def baseline(obj):
    return digest([obj['identity'], obj['name'], obj.get('definition'), obj.get('unique_id'), obj['tags']])


def with_component_values(group, proposal, *, simulation=False):
    """Use only current reviewed values, or explicitly requested draft simulation."""
    if not proposal or proposal.baseline != baseline(group):
        return group
    accepted = proposal.state == 'reviewed'
    if not accepted and not (simulation and proposal.state == 'draft'):
        return group
    return dict(group, reviewed_components=accepted, proposed_scopes=set(proposal.tags),
                component_source='Reviewed value' if accepted else 'Draft simulation',
                tags=[t for t in group['tags'] if t.get('scope') not in proposal.tags]
                     + [{'scope': key, 'tag': value} for key, value in proposal.tags.items()])


def queue_counts(rows):
    return {
        'attention': sum(bool(row['needs_attention']) for row in rows),
        'compliant': sum(row['status'] == 'Compliant' and not row['needs_attention'] for row in rows),
        'exceptions': sum(row['status'] == 'Reviewed exception' and not row['needs_attention'] for row in rows),
        'all': len(rows),
    }


def in_queue(row, tab):
    return (tab == 'all' or tab == 'attention' and row['needs_attention']
            or tab == 'compliant' and row['status'] == 'Compliant' and not row['needs_attention']
            or tab == 'exceptions' and row['status'] == 'Reviewed exception' and not row['needs_attention'])


def _explicit_single_ip_value(group):
    """Read one saved IP entry; reviewed values cannot override membership."""
    definition = group.get('definition')
    if not isinstance(definition, dict) or definition.get('extended_expression'):
        return None
    expressions = definition.get('expression')
    if not isinstance(expressions, list) or len(expressions) != 1:
        return None
    expression = expressions[0]
    if not isinstance(expression, dict) or expression.get('resource_type') != 'IPAddressExpression':
        return None
    addresses = expression.get('ip_addresses')
    if not isinstance(addresses, list) or len(addresses) != 1 or not isinstance(addresses[0], str):
        return None
    value = addresses[0].strip()
    # Scoped IPv6 literals identify an interface, not a portable NSX host address.
    if '%' in value:
        return None
    return value


def explicit_host_ip(group):
    value = _explicit_single_ip_value(group)
    if value is None:
        return None
    try:
        if '/' in value:
            host = ipaddress.ip_interface(value)
            if host.network.prefixlen != host.max_prefixlen:
                return None
            return str(host.ip)
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def explicit_subnet(group):
    """Canonicalize exactly one explicit CIDR; host prefixes belong to host rules."""
    value = _explicit_single_ip_value(group)
    if value is None or '/' not in value:
        return None
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None
    return str(network) if network.prefixlen < network.max_prefixlen else None


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
                entries = node.get(key, [])
                if not isinstance(entries, list):
                    notes.append('Malformed explicit membership'); continue
                for value in entries:
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
    return dict(tag_condition_count=sum(n.get('key') == 'Tag' for n in dynamic), group_type=kind, tags=tags if kind == 'dynamic' and not needs_review else [],
                candidate_tags=tags, conditions=[f"{n.get('member_type', '')} {n.get('key', '')} {n.get('operator', '')} {n.get('value', '')}" for n in dynamic], members=members, nested=nested, evidence_notes=notes, needs_review=needs_review)


def inventory_snapshot(environment):
    return environment.snapshots.filter(testing=False, imported=False).defer('report','html','summary').annotate(
        inventory_warning=F('summary__inventory_warnings')).first()


def inventory(environment, snapshot=None):
    snapshot = snapshot or inventory_snapshot(environment)
    if not snapshot:
        return None, [], 'No full collection is available. Collect this environment before evaluating names.'
    if snapshot.browsing_status != 'ready':
        return snapshot, [], 'Snapshot browsing data is archived or being prepared. Restore its browsing data first.'
    from .analysis_index import load
    records = load(snapshot,'groups')['groups']
    groups = []
    for r in records:
        groups.append(dict(id=r['id'], identity=r['path'], name=r['name'], definition=r['definition'],
                           unique_id=r['unique_id'], **r['evidence']))
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
    warning = 'Inventory coverage has warnings; results are provisional and cannot establish compliance.' if snapshot.inventory_warning else ''
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
    host_ip = explicit_host_ip(group)
    subnet = explicit_subnet(group)
    candidates = []
    for policy in policies:
        config = policy.configuration
        if config.get('target') != 'group' or config.get('group_type', 'all') not in ('all', group.get('group_type')):
            continue
        match_field = config.get('match_field')
        if config.get('match_mode') == 'single_host_ip':
            if not host_ip:
                continue
        elif config.get('match_mode') == 'single_subnet':
            if not subnet:
                continue
        elif match_field:
            component = next((c for c in config['components'] if c['field'] == match_field), None)
            if not component: continue
            observed = values[scope(component, bindings.get(policy.pk, {}))]
            if config.get('match_mode', 'exact') == 'exists':
                if not any(observed): continue
            elif config.get('match_value') not in observed:
                continue
        candidates.append(policy)
    # Explicit environment overrides precede global rules; category rules precede defaults.
    local = [p for p in candidates if p.environment_id]
    candidates = local or candidates
    typed = [p for p in candidates if p.configuration.get('group_type', 'all') != 'all']
    candidates = typed or candidates
    specific = [p for p in candidates if p.configuration.get('match_field') or p.configuration.get('match_mode') in ('single_host_ip', 'single_subnet')]
    candidates = specific or candidates
    exact = [p for p in candidates if p.configuration.get('match_mode') in ('single_host_ip', 'single_subnet') or p.configuration.get('match_field') and p.configuration.get('match_mode', 'exact') == 'exact']
    candidates = exact or candidates
    row = dict(group, proposed='', policy=None, status='No applicable policy', reasons=[], components=[], matching_policy_ids=[str(p.pk) for p in candidates])
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
        if source == 'single_host_ip':
            raw = [host_ip] if host_ip else []
        elif source == 'single_subnet':
            raw = [subnet.replace('/', '_')] if subnet else []
        elif source == 'fixed':
            raw = [component.get('literal', '')]
        elif source == 'identity':
            raw = [group['identity'].rsplit('/', 1)[-1]]
        elif source == 'type':
            raw = [group.get('group_type', 'unknown')]
        else:
            raw = sorted(values[actual_scope])
        reason = ''
        output = ''
        if len(raw) > 1:
            reason = 'Ambiguous tags'
        elif not raw or not raw[0]:
            if component['required']:
                reason = {'single_host_ip': 'Single host IP required', 'single_subnet': 'Single subnet required'}.get(source, 'Missing required tags')
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
            if component['format'] == 'ip_address':
                try:
                    valid = '%' not in output and str(ipaddress.ip_address(output)) == output
                except ValueError:
                    valid = False
            elif component['format'] == 'ip_subnet':
                try:
                    network = ipaddress.ip_network(output.replace('_', '/'), strict=True)
                    valid = '%' not in output and str(network).replace('/', '_') == output and network.prefixlen < network.max_prefixlen
                except ValueError:
                    valid = False
            else:
                pattern = {'alphanumeric': r'[A-Za-z0-9]+', 'digits': r'[0-9]+', 'extended': r'[A-Za-z0-9_.-]+'}[component['format']]
                valid = bool(re.fullmatch(pattern, output))
            if not valid or len(output) > component['max_length']:
                reason = 'Invalid value'
        row['components'].append({'field': component['field'], 'source': 'Explicit IP membership' if source in ('single_host_ip', 'single_subnet') else group.get('component_source') if source == 'tag' and actual_scope in group.get('proposed_scopes', set()) else 'Definition tag' if source == 'tag' else source, 'scope': actual_scope if source == 'tag' else source, 'observed': ', '.join(raw) or 'Missing', 'output': output, 'issue': reason})
        if reason:
            issues.append(reason)
            row['reasons'].append(component['field'] + ': ' + reason)
        if output:
            parts.append(output)
    if issues:
        row['status'] = next(label for label in ['Ambiguous tags', 'Single host IP required', 'Single subnet required', 'Missing required tags', 'Invalid value'] if label in issues)
        return row
    proposed = config['separator'].join(parts)
    if not proposed or len(proposed) > config['max_length']:
        row.update(status='Invalid value', reasons=['Result is empty or exceeds the maximum name length.'])
        return row
    row.update(proposed=proposed, status='Compliant' if proposed == group['name'] else 'Name differs')
    return row


def assess(environment, replacement=None, simulation=False):
    snapshot = inventory_snapshot(environment)
    policies = list(NamingPolicy.objects.filter(enabled=True, removed_at__isnull=True).filter(Q(environment__isnull=True) | Q(environment=environment)))
    if replacement:
        policies = [p for p in policies if p.pk != replacement.pk]
        if replacement.environment_id in (None, environment.pk): policies.append(replacement)
    bindings = {b.policy_id: (b.draft_scopes if replacement and b.policy_id == replacement.pk else b.scopes) for b in NamingScopeBinding.objects.filter(environment=environment)}
    proposals = {p.group_path: p for p in NamingProposal.objects.filter(environment=environment, object_type='group').select_related('owner', 'reviewed_by')}
    from .models import SnapshotNamingAssessment
    from .analysis_index import VERSION
    signature = digest(['naming-assessment-v3', VERSION, str(snapshot.pk) if snapshot else None,
                        snapshot.browsing_status if snapshot else None, bool(snapshot.inventory_warning) if snapshot else False,
                        str(timezone.localdate()), simulation,
                        [(p.pk,p.revision,p.name,p.configuration) for p in policies], bindings,
                        [{k:v for k,v in p.__dict__.items() if k!='_state'} for p in proposals.values()]])
    if snapshot and snapshot.browsing_status=='ready' and replacement is None:
        cached=SnapshotNamingAssessment.objects.filter(snapshot=snapshot,simulation=simulation,signature=signature).values_list('rows',flat=True).first()
        if cached is not None:
            by_policy={p.pk:p for p in policies}
            for row in cached:
                row['policy']=by_policy.get(row.pop('_policy_id'))
                row['proposal']=proposals.get(row['identity'])
            warning='Inventory coverage has warnings; results are provisional and cannot establish compliance.' if snapshot.inventory_warning else ''
            if not cached: warning='No indexed groups found. Check collection coverage and browsing preparation.'
            return snapshot,cached,warning
    snapshot, groups, warning = inventory(environment, snapshot)
    rows = []
    for group in groups:
        proposal = proposals.get(group['identity'])
        stale = bool(proposal and proposal.baseline != baseline(group))
        effective = with_component_values(group, proposal, simulation=simulation)
        row = evaluate(effective, policies, bindings)
        row.update(observed_status=evaluate(group, policies, bindings)['status'] if effective is not group else row['status'], proposal=proposal, stale=stale, observed_tags=group['tags'], baseline=baseline(group))
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
        work = proposal.workflow if proposal else {}
        definition_stamp = digest([row['identity'], row.get('definition'), row.get('unique_id'), row['observed_tags']])
        row['definition_stamp'] = definition_stamp
        changed = bool(work and (work.get('policy_stamp') != policy_stamp or work.get('definition_stamp') != definition_stamp))
        resolved = bool(work.get('state') == 'awaiting' and not changed and not warning and not simulation and snapshot and str(snapshot.pk) != work.get('snapshot') and snapshot.generated_at.isoformat() > work.get('snapshot_time', '') and row['status'] == 'Compliant' and row['observed_status'] == 'Compliant' and row['name'] == work.get('proposed'))
        row['work_stale'] = changed
        row['progress_label'] = 'Resolved' if resolved else 'Review required — evidence changed' if changed else {'in_review':'In review','prepared':'Change prepared','awaiting':'Awaiting verification'}.get(work.get('state'), 'In review' if proposal and proposal.owner_id else 'Unassigned')
        row['next_action'] = {'Name differs':'Review name correction','Ambiguous policy':'Resolve conflicting policies','No applicable policy':'Configure a naming policy','Duplicate proposed name':'Resolve name collision','Evidence incomplete':'Review collection coverage'}.get(row['status'], 'Review component evidence')

        # Compare complete name segments so incidental shared letters do not fragment the evidence.
        before_parts = re.findall(r'[^-_.\s]+|[-_.\s]+', row['name'])
        after_parts = re.findall(r'[^-_.\s]+|[-_.\s]+', row['proposed'] or '')
        row['name_diff'] = [{'changed':tag!='equal', 'old':''.join(before_parts[a:b]), 'new':''.join(after_parts[c:d])} for tag,a,b,c,d in SequenceMatcher(None, before_parts, after_parts, autojunk=False).get_opcodes()] if row['proposed'] else []
        proposal = row['proposal']
        row['decision_stamp'] = digest([row['baseline'], policy_stamp, [proposal.state, proposal.tags, proposal.note, str(proposal.updated_at)] if proposal else None])
        row['exception_stale'] = bool(proposal and proposal.state == 'exception' and (row['stale'] or proposal.exception_policy_stamp != policy_stamp or not proposal.owner_id or not proposal.expires_at or proposal.expires_at < timezone.localdate()))
        if proposal and proposal.state == 'exception' and not row['exception_stale']:
            row['reasons'].append('Reviewed exception: ' + proposal.note)
            row['status'] = 'Reviewed exception'
        if proposal and proposal.state == 'exception' and row['exception_stale']:
            row['reasons'].append('Exception expired or evidence changed; review it again.')
        row['needs_attention'] = row['status'] not in ('Compliant','Reviewed exception') or row['work_stale'] or row['exception_stale']
    if snapshot and snapshot.browsing_status=='ready' and replacement is None:
        stored=[]
        for row in rows:
            payload={key:value for key,value in row.items() if key not in ('policy','proposal','proposed_scopes')}
            payload['_policy_id']=row['policy'].pk if row['policy'] else None
            if 'proposed_scopes' in row: payload['proposed_scopes']=sorted(row['proposed_scopes'])
            stored.append(payload)
        SnapshotNamingAssessment.objects.update_or_create(snapshot=snapshot,simulation=simulation,defaults={'signature':signature,'rows':stored})
    return snapshot, rows, warning


def counts(rows):
    return sorted(Counter(row['status'] for row in rows).items())
