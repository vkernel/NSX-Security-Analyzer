"""Read-only, bounded configuration dependency previews for saved snapshots."""
from collections import defaultdict
import json
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET
from .models import Snapshot, Finding


def preview(report, path, limit=200, depth_limit=8, action="delete", scope="", tag="", vm=""):
    nodes, incoming, children = {}, defaultdict(set), defaultdict(set)
    warnings = ['Configuration dependencies only. This is not a traffic simulation or deletion approval.',
                'Search can omit non-indexed or permission-hidden references. No detected references does not mean safe to delete.']
    groups = report.get('inventory', {}).get('groups')
    if groups is None:
        groups = [r for r in report.get('objects', []) if r.get('kind') == 'group']
    dfw = report.get('dfw', {})
    for kind, rows in [('group', groups), ('service', report.get('inventory', {}).get('services', [])), ('rule', dfw.get('rules', [])), ('policy', dfw.get('policies', []))]:
        for row in rows:
            if row.get('path'):
                nodes[row['path']] = dict(row, impact_kind=kind)
    def paths(value):
        if isinstance(value, str) and value.startswith('/infra/'):
            yield value
        elif isinstance(value, list):
            for child in value:
                yield from paths(child)
        elif isinstance(value, dict):
            # Only expression paths, not object metadata or arbitrary strings.
            for key in ('paths', 'expressions'):
                yield from paths(value.get(key))
    for source, row in list(nodes.items()):
        config = row.get('configuration') or {}
        if row['impact_kind'] == 'group':
            if not config:
                warnings.append('Some group definitions are unavailable; nested chains may be incomplete.')
            for target in paths(config.get('expression')):
                incoming[target].add((source, 'Nested group membership'))
        if row['impact_kind'] == 'rule':
            rule = config.get('rule', config)
            if not rule:
                warnings.append('Some rule definitions are unavailable; field-level references may be incomplete.')
            for field, label in [('source_groups', 'Rule source'), ('destination_groups', 'Rule destination'),
                                 ('scope', 'Rule applied-to'), ('services', 'Rule service')]:
                for target in paths(rule.get(field)):
                    incoming[target].add((source, label))
            for target in paths(config.get('policy', {}).get('scope')):
                incoming[target].add((source, 'Policy applied-to scope'))
            policy = row.get('policy_path')
            if policy:
                incoming[source].add((policy, 'Containing policy'))
                children[policy].add(source)
        for source_ref in row.get('referenced_by', []):
            if isinstance(source_ref, str) and not any(s == source_ref for s, _ in incoming[source]):
                incoming[source].add((source_ref, 'Collected configuration reference'))
    tags = report.get('tags', {})
    vm_nodes = {}
    for item in tags.get('virtual_machines', []):
        identity = item.get('path') or item.get('external_id') or item.get('id')
        if identity:
            vm_nodes[identity] = dict(item, path=identity, name=item.get('display_name') or identity, impact_kind='VM')
    assignments = []
    if action == 'remove-tag':
        selected = next((item for item in tags.get('objects', []) if item.get('scope', '') == scope and item.get('name') == tag), None)
        if selected is None or (vm and vm not in selected.get('vms', {})):
            raise KeyError('Tag assignment not found')
        path = 'tag:' + json.dumps([scope, tag])
        nodes[path] = {'path':path, 'name':scope + ' / ' + tag, 'impact_kind':'tag'}
        assignments = [{'path':key, 'name':value} for key,value in selected.get('vms', {}).items()]
        for group in selected.get('group_conditions', {}):
            incoming[path].add((group, 'Potential tag-condition membership change'))
        if vm:
            nodes[path]['name'] += ' on ' + selected['vms'][vm]
        warnings.append('Tag conditions are potential dependencies, not proof of realized membership. AND/OR, negative conditions and other criteria can preserve or change the result.')
        if not vm:
            warnings.append('Select a VM to preview one assignment removal. The overview includes all collected uses of this tag; it does not model deletion of a tag catalog entry.')
    elif action == 'delete-vm':
        if path not in vm_nodes:
            raise KeyError(path)
        nodes[path] = vm_nodes[path]
        for item in tags.get('objects', []):
            if path in item.get('vms', {}):
                for group in item.get('group_conditions', {}):
                    incoming[path].add((group, 'Potential membership via tag: ' + item.get('scope', '') + ' / ' + item.get('name', '')))
        # Explicit paths remain distinguishable from inferred tag relationships.
        for group in groups:
            if path in set(paths((group.get('configuration') or {}).get('expression'))):
                incoming[path].discard((group['path'], 'Nested group membership'))
                incoming[path].add((group['path'], 'Explicit VM path reference'))
        warnings.append('VM relationships cover collected explicit paths and tag conditions. Other dynamic criteria, network attachments and external consumers are not fully modeled. Membership changes are not verified.')
    elif action != 'delete':
        raise KeyError('Unsupported action')
    if path not in nodes:
        raise KeyError(path)
    if action in ('delete-vm', 'remove-tag') and (not tags.get('vm_inventory_complete') or tags.get('errors') or tags.get('unsupported_conditions')):
        warnings.append('VM/tag coverage is incomplete or contains unsupported conditions; additional dependencies may exist.')
    root = nodes[path]
    if report.get('testing') or report.get('inventory_warnings') or report.get('imported'):
        warnings.append('This report is sampled, imported or has inventory warnings; coverage needs review.')
    if not report.get('search_coverage') or report.get('search_coverage', {}).get('mode') != 'all_types' or report.get('search_coverage', {}).get('errors') or report.get('search_coverage', {}).get('failed'):
        warnings.append('Complete reference-search coverage has not been established.')
    if dfw.get('errors'):
        warnings.append('Firewall collection reported errors; policy and rule coverage may be incomplete.')
    seen = {path}
    count = 0
    truncated = False
    def walk(current, depth):
        nonlocal count, truncated
        edges = set(incoming.get(current, ()))
        if current == path and root['impact_kind'] == 'policy':
            edges.update((child, 'Rule removed with policy') for child in children[current])
        result = []
        for target, label in sorted(edges):
            if count >= limit or depth >= depth_limit:
                truncated = True
                break
            count += 1
            row = nodes.get(target, {})
            repeated = target in seen
            seen.add(target)
            result.append({'path': target, 'name': row.get('name') or target,
                           'kind': row.get('impact_kind', 'Unresolved reference'), 'label': label,
                           'repeated': repeated, 'known': bool(row),
                           'children': [] if repeated else walk(target, depth + 1)})
        return result
    tree = walk(path, 0)
    dependencies = []
    for target, edges in incoming.items():
        for source, label in edges:
            if source == path and label != 'Containing policy':
                dependencies.append({'path': target, 'name': nodes.get(target, {}).get('name', target), 'label': label})
    consequences = {
        'service': 'Deleting this service removes its service entries. Referencing firewall rules or nested services may require changes; no traffic behavior can be inferred from absence of references.',
        'VM': 'Deleting the VM removes the workload. Related groups may change membership and their referencing rules may apply to a different set of workloads. Groups and rules are not deleted automatically.',
        'tag': 'Removing this tag assignment may change dynamic group membership and the rules that apply to the VM. It does not delete the VM, groups, rules, or other assignments.',
        'group': 'Referencing rules or parent groups may require changes. Deletion may be rejected while references exist. The resulting traffic behavior cannot be determined here.',
        'rule': 'Removing this rule changes policy evaluation. Traffic may match a later rule or the default action. Referenced groups and services are not deleted with the rule.',
        'policy': 'Removing this policy removes its contained rules. Traffic may match other policies or default actions. Referenced groups and services remain separate objects.',
    }
    if len(dependencies) > limit:
        warnings.append('The outgoing reference list was truncated; additional dependencies may exist.')
    if truncated:
        warnings.append('The dependency chain was truncated at the preview size or depth limit; additional impact may exist.')
    return {'root': root, 'tree': tree, 'dependencies': dependencies[:limit], 'warnings': list(dict.fromkeys(warnings)),
            'consequence': consequences[root['impact_kind']], 'count': count, 'action':action,
            'assignments':assignments[:limit], 'assignments_truncated':len(assignments)>limit, 'selected_vm':vm, 'scope':scope, 'tag':tag}


@login_required
@require_GET
def impact_preview(request, pk):
    snapshot = get_object_or_404(Snapshot.objects.select_related('environment'), pk=pk)
    path = request.GET.get('path', '')
    try:
        context = preview(snapshot.report, path, action=request.GET.get('action', 'delete'), scope=request.GET.get('scope', ''), tag=request.GET.get('tag', ''), vm=request.GET.get('vm', ''))
    except KeyError:
        raise Http404('This object or assignment is not present in the selected snapshot.')
    finding = None
    finding_id = request.GET.get('finding', '')
    if finding_id.isdecimal():
        finding = get_object_or_404(Finding, pk=finding_id, environment_id=snapshot.environment_id, path=path, snapshot_id=pk)
        request.session['impact_preview_' + str(finding.pk)] = str(snapshot.pk)
    def links(entries):
        for entry in entries:
            entry['snapshot_url'] = reverse('snapshot', args=[pk]) + '?' + urlencode({'object': entry['path']})
            entry['impact_url'] = reverse('impact-preview', args=[pk]) + '?' + urlencode({'path': entry['path']})
            links(entry.get('children', []))
    links(context['tree']); links(context['dependencies'])
    latest = Snapshot.objects.filter(environment_id=snapshot.environment_id, testing=False, imported=False).order_by('-generated_at', '-created_at').values_list('pk', flat=True).first()
    context.update(snapshot=snapshot, environment=snapshot.environment, finding=finding, stale=latest != snapshot.pk,
                   object_url=reverse('snapshot', args=[pk]) + '?' + urlencode({'object': path}))
    if context['action'] == 'delete-vm':
        context['object_url'] += '&view=vms'
    elif context['action'] == 'remove-tag':
        context['object_url'] = reverse('snapshot', args=[pk]) + '?' + urlencode({'object':context['root']['path'], 'view':'tags', 'tag':context['tag'], 'scope':context['scope']})
    response = render(request, 'inventory/impact_preview.html', context)
    response['Cache-Control'] = 'private, no-store'
    return response
