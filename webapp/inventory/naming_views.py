import copy
import csv
import io
from collections import Counter
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from . import naming
from .audit_events import record
from .models import Environment, NamingPolicy, NamingPolicyVersion, NamingScopeBinding, NamingProposal, SnapshotRecord
from .naming_forms import PolicyForm, ComponentSet, BindingForm, ProposalForm, configuration

DEFAULT = {'target': 'group', 'group_type': 'all', 'separator': '-', 'max_length': 63, 'match_field': '', 'match_value': '', 'components': [
    {'field': field, 'source': 'tag', 'scope': field, 'literal': '', 'required': True, 'case': 'upper', 'format': 'alphanumeric', 'max_length': 32, 'mapping': {}}
    for field in ['Environment', 'Location', 'Application', 'Role', 'Instance']]}


def snapshot_version(policy, actor, action):
    policy.revision += 1
    policy.save()
    config = {'name': policy.name, 'environment': policy.environment_id, 'draft': policy.draft,
              'configuration': policy.configuration, 'enabled': policy.enabled,
              'bindings': list(policy.bindings.order_by('environment_id').values('environment_id', 'scopes', 'draft_scopes'))}
    NamingPolicyVersion.objects.create(policy=policy, revision=policy.revision, actor=actor, action=action, configuration=config)
    record('naming.policy_' + action, 'NamingPolicy', policy.pk, actor=actor.pk, details={'revision': policy.revision})


def impact(policy):
    replacement = copy.copy(policy)
    replacement.configuration = policy.draft
    environments = Environment.objects.filter(pk=policy.environment_id) if policy.environment_id else Environment.objects.all()
    summaries, examples, stamp = [], [], []
    for env in environments:
        old_snapshot, before, old_warning = naming.assess(env)
        snapshot, after, warning = naming.assess(env, replacement=replacement)
        previous = {r['identity']: r for r in before}
        transitions = Counter()
        for row in after:
            old = previous.get(row['identity'], {})
            transitions[(old.get('status', 'New group'), row['status'])] += 1
            if len(examples) < 40 and (old.get('proposed') != row['proposed'] or old.get('status') != row['status'] or row['status'] != 'Compliant'):
                examples.append({'environment': env.name, 'name': row['name'], 'before': old.get('proposed', ''), 'after': row['proposed'], 'status': row['status'], 'reasons': row['reasons']})
        summaries.append({'environment': env, 'counts': naming.counts(after), 'total': len(after), 'warning': warning,
                          'transitions': [{'before': a, 'after': b, 'count': count} for (a, b), count in sorted(transitions.items())]})
        stamp.append([env.pk, str(old_snapshot.pk) if old_snapshot else '', str(snapshot.pk) if snapshot else '', warning,
                      [(r['identity'], r['baseline'], r['status'], r['proposed']) for r in after]])
    # Includes other policy and mapping edits even if they currently produce identical names.
    fingerprint = naming.digest([policy.revision, policy.draft, stamp,
        list(NamingPolicy.objects.order_by('pk').values('pk', 'revision')),
        list(NamingScopeBinding.objects.order_by('pk').values('policy_id', 'environment_id', 'scopes', 'draft_scopes'))])
    return {'summaries': summaries, 'examples': examples, 'fingerprint': fingerprint}


@login_required
@require_http_methods(['GET', 'POST'])
def policies(request, pk=None):
    if not request.user.is_superuser: return HttpResponseForbidden('Administrator access is required.')
    policy = get_object_or_404(NamingPolicy, pk=pk) if pk else None
    config = policy.draft if policy else DEFAULT
    if policy and request.method == 'GET' and request.GET.get('version', '').isdigit():
        version = get_object_or_404(policy.versions, revision=request.GET['version'])
        config = version.configuration['draft']
    initial = dict(config, name=policy.name if policy else '', environment=policy.environment_id if policy else None, revision=policy.revision if policy else 0)
    components = [dict(c, mapping_text='\n'.join(f'{a}={b}' for a,b in c.get('mapping', {}).items())) for c in config.get('components', [])]
    editing = request.method == 'POST' and request.POST.get('action') == 'save'
    form = PolicyForm(request.POST if editing else None, initial=initial)
    if policy: form.fields['environment'].disabled = True
    formset = ComponentSet(request.POST if editing else None, initial=components, prefix='components')
    binding_form = BindingForm(initial={'revision': policy.revision if policy else 0})
    preview = None
    error = ''
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save' and form.is_valid() and formset.is_valid():
            config = configuration(form, formset)
            if form.is_valid():
                with transaction.atomic():
                    if policy:
                        policy = NamingPolicy.objects.select_for_update().get(pk=policy.pk)
                    if policy and policy.revision != form.cleaned_data['revision']:
                        error = 'The policy changed. Reload before saving; your submitted draft is shown below.'
                    else:
                        policy = policy or NamingPolicy()
                        policy.name = form.cleaned_data['name']
                        policy.environment = form.cleaned_data['environment']
                        policy.draft = config
                        policy.save()
                        snapshot_version(policy, request.user, 'draft_saved')
                        messages.success(request, 'Draft saved. Active naming rules are unchanged. Preview the impact before activation.')
                        return redirect('naming-policy-edit', pk=policy.pk)
        elif policy and action == 'binding':
            binding_form = BindingForm(request.POST)
            if binding_form.is_valid():
                data = binding_form.cleaned_data
                with transaction.atomic():
                    policy = NamingPolicy.objects.select_for_update().get(pk=policy.pk)
                    fields = {c['field'] for c in policy.draft['components'] if c['source'] == 'tag'}
                    if policy.revision != data['revision']:
                        error = 'The policy changed. Reload before editing mappings.'
                    elif data['field'] not in fields or policy.environment_id not in (None, data['environment'].pk):
                        error = 'Choose a tag field from this policy and an environment it covers.'
                    else:
                        binding, _ = NamingScopeBinding.objects.get_or_create(policy=policy, environment=data['environment'])
                        binding.draft_scopes = {**binding.draft_scopes, data['field']: data['scope']}
                        binding.save()
                        snapshot_version(policy, request.user, 'mapping_saved')
                        messages.success(request, 'Draft scope mapping saved. Preview and activate to use it.')
                        return redirect('naming-policy-edit', pk=policy.pk)
        elif policy and action in ('preview', 'activate') and policy.draft.get('target') != 'group':
            error = 'This is a legacy VM policy. Review the group type and components, then save a group policy draft before previewing.'
        elif policy and action in ('preview', 'activate'):
            with transaction.atomic():
                policy = NamingPolicy.objects.select_for_update().get(pk=policy.pk)
                preview = impact(policy)
                key = 'naming-preview-' + str(policy.pk)
                if action == 'preview':
                    request.session[key] = preview['fingerprint']
                elif request.POST.get('acknowledge') != 'yes':
                    error = 'Acknowledge the impact before activating.'
                elif request.session.get(key) != preview['fingerprint']:
                    error = 'Policy, mappings or inventory changed, or no preview was reviewed. Preview again before activation.'
                    request.session.pop(key, None)
                else:
                    policy.configuration = policy.draft
                    policy.enabled = True
                    for binding in policy.bindings.all():
                        binding.scopes = binding.draft_scopes
                        binding.save(update_fields=['scopes'])
                    snapshot_version(policy, request.user, 'activated')
                    request.session.pop(key, None)
                    messages.success(request, 'Naming policy activated for read-only assessment. No NSX or group configuration was changed.')
                    return redirect('naming-policy-edit', pk=policy.pk)
        elif policy and action == 'disable':
            with transaction.atomic():
                policy = NamingPolicy.objects.select_for_update().get(pk=policy.pk)
                if request.POST.get('revision') != str(policy.revision):
                    error = 'Policy changed. Reload before disabling.'
                else:
                    policy.enabled = False
                    snapshot_version(policy, request.user, 'disabled')
                    messages.success(request, 'Naming assessment policy disabled. Observed tags and group names are unchanged.')
                    return redirect('naming-policy-edit', pk=policy.pk)
    return render(request, 'inventory/naming_policy.html', {'policies': NamingPolicy.objects.select_related('environment'), 'policy': policy,
        'form': form, 'formset': formset, 'binding_form': binding_form, 'preview': preview, 'error': error,
        'bindings': policy.bindings.select_related('environment') if policy else [], 'versions': policy.versions.select_related('actor')[:30] if policy else []})


def csv_response(rows):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="naming-assessment.csv"'
    writer = csv.writer(response)
    writer.writerow(['group_path', 'group_type', 'current_name', 'proposed_name', 'policy', 'status', 'issues'])
    for row in rows:
        values = [row['identity'], row['group_type'], row['name'], row['proposed'], row['policy'].name if row['policy'] else '', row['status'], '; '.join(row['reasons'])]
        writer.writerow(["'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value for value in values])
    return response


@login_required
@require_http_methods(['GET'])
def index(request):
    environment = Environment.objects.filter(pk=request.GET.get('environment')).first() if request.GET.get('environment', '').isdigit() else Environment.objects.first()
    if environment: return redirect('naming-compliance', pk=environment.pk)
    return render(request, 'inventory/section_empty.html', {'section_name': 'Naming compliance'})


@login_required
@require_http_methods(['GET', 'POST'])
def compliance(request, pk):
    environment = get_object_or_404(Environment, pk=pk)
    simulation = request.GET.get('simulate') == '1'
    snapshot, rows, warning = naming.assess(environment, simulation=simulation)
    proposal_form = ProposalForm(request.POST or None, request.FILES or None)
    error = ''
    if request.method == 'POST':
        if not request.user.is_staff: return HttpResponseForbidden('Operator access is required for proposals.')
        action = request.POST.get('action')
        # Always bind proposals to observed data, never to a previous simulation.
        if simulation: snapshot, rows, warning = naming.assess(environment)
        by_id = {str(row['id']): row for row in rows}
        ids = request.POST.getlist('selected')
        selected = [by_id[i] for i in set(ids) if i in by_id]
        try:
            if request.POST.get('snapshot') != (str(snapshot.pk) if snapshot else ''):
                raise ValidationError('Inventory changed. Reload the assessment before saving proposals.')
            owner_id = request.POST.get('owner', '')
            owner = get_object_or_404(get_user_model(), pk=owner_id, is_active=True, is_staff=True) if owner_id.isdigit() else None
            if owner_id and not owner: raise ValidationError('Choose an active operator as owner.')
            changes = {}
            if action == 'import':
                if not proposal_form.is_valid(): raise ValidationError('Check the proposal fields.')
                upload = proposal_form.cleaned_data.get('csv_file')
                if not upload or upload.size > 1024*1024: raise ValidationError('Choose a UTF-8 CSV file up to 1 MB.')
                reader = csv.DictReader(io.StringIO(upload.read().decode('utf-8-sig')))
                if not {'group_path', 'scope', 'value'}.issubset(reader.fieldnames or []): raise ValidationError('CSV needs group_path,scope,value columns.')
                identities = {r['identity']: r for r in rows}
                selected_by_id = {}
                for n, item in enumerate(reader):
                    if n >= 1000: raise ValidationError('Import at most 1,000 CSV rows at a time.')
                    identity, scope, value = ((item.get(k) or '').strip() for k in ['group_path', 'scope', 'value'])
                    if identity not in identities or not scope or len(scope)>128 or not value or len(value)>256: raise ValidationError(f'CSV row {n+2} has an unknown group or invalid scope/value.')
                    row = identities[identity]
                    selected_by_id[row['id']] = row
                    mapping = changes.setdefault(row['identity'], {})
                    if scope in mapping: raise ValidationError(f'CSV row {n+2} repeats a scope for the same group.')
                    mapping[scope] = value
                selected = list(selected_by_id.values())
            elif action == 'propose':
                if not proposal_form.is_valid() or not proposal_form.cleaned_data.get('scope') or not proposal_form.cleaned_data.get('value'):
                    raise ValidationError('Enter a scope and proposed value.')
                changes = {r['identity']: {proposal_form.cleaned_data['scope']: proposal_form.cleaned_data['value']} for r in selected}
            if not selected or (action != 'import' and (len(selected) != len(set(ids)) or len(selected)>100)):
                raise ValidationError('Select 1–100 current groups, or import a CSV for current group paths.')
            if action not in ('propose', 'import', 'assign', 'review', 'reject', 'exception'): raise ValidationError('Choose a supported proposal action.')
            if action == 'exception' and (not request.user.is_superuser or not proposal_form.is_valid() or not proposal_form.cleaned_data.get('note')):
                raise ValidationError('An administrator must record a reason for a naming exception.')
            if action == 'assign' and not owner: raise ValidationError('Select an owner.')
            with transaction.atomic():
                Environment.objects.select_for_update().get(pk=environment.pk)
                if environment.snapshots.filter(testing=False, imported=False).values_list('pk', flat=True).first() != snapshot.pk:
                    raise ValidationError('A new collection arrived. Reload before saving proposals.')
                for row in selected:
                    proposal = NamingProposal.objects.select_for_update().filter(environment=environment, object_type='group', group_path=row['identity']).first()
                    if action in ('review', 'exception', 'reject'):
                        stamp = naming.digest([row['baseline'], row['policy_stamp'], [proposal.state, proposal.tags, proposal.note, str(proposal.updated_at)] if proposal else None])
                        if request.POST.get('decision_stamp_' + str(row['id'])) != stamp:
                            raise ValidationError('The proposal or naming policy changed. Reload and review the current evidence before recording a decision.')
                    if action in ('propose', 'import'):
                        proposal = proposal or NamingProposal(environment=environment, group_path=row['identity'])
                        # Do not silently carry stale proposed fields into a fresh review.
                        if proposal.baseline and proposal.baseline != row['baseline']:
                            raise ValidationError('An existing proposal is stale. Reject it first, then prepare fresh component values.')
                        proposal.tags = {**proposal.tags, **changes[row['identity']]}
                        proposal.reviewed_by = None
                        proposal.baseline, proposal.state, proposal.author = row['baseline'], 'draft', request.user
                        proposal.note = proposal_form.cleaned_data.get('note', '')
                        if owner: proposal.owner = owner
                    elif action == 'exception':
                        proposal = proposal or NamingProposal(environment=environment, group_path=row['identity'])
                        proposal.state, proposal.baseline = 'exception', row['baseline']
                        proposal.exception_policy_stamp = row['policy_stamp']
                        proposal.note = proposal_form.cleaned_data['note']
                        proposal.reviewed_by = request.user
                    elif action == 'assign':
                        proposal = proposal or NamingProposal(environment=environment, group_path=row['identity'], baseline=row['baseline'], author=request.user)
                        proposal.owner = owner
                    else:
                        if not proposal: raise ValidationError('Prepare a proposal before reviewing it.')
                        if action == 'review' and (proposal.baseline != row['baseline'] or not proposal.tags): raise ValidationError('Stale or empty proposals cannot be marked reviewed.')
                        proposal.state = 'reviewed' if action == 'review' else 'rejected'
                        proposal.reviewed_by = request.user if action == 'review' else None
                        if action == 'reject':
                            proposal.tags = {}
                            proposal.baseline = row['baseline']
                    if len(proposal.group_path)>512 or len(proposal.tags)>128:
                        raise ValidationError('A group path must fit 512 characters and proposals may contain at most 128 scopes.')
                    proposal.save()
                    record('naming.proposal_' + action, 'NamingProposal', proposal.pk, actor=request.user.pk,
                           details={'environment_id': environment.pk, 'group_path': row['identity'], 'state': proposal.state, 'tags': proposal.tags, 'owner_id': proposal.owner_id, 'note': proposal.note, 'reviewed_by_id': proposal.reviewed_by_id, 'baseline': proposal.baseline, 'exception_policy_stamp': proposal.exception_policy_stamp})
            messages.success(request, f'{len(selected)} proposal(s) updated. No tags or group names were changed in NSX or vCenter.')
            return redirect('naming-compliance', pk=environment.pk)
        except (ValidationError, UnicodeError, csv.Error) as exc:
            error = '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'Cannot read this UTF-8 CSV file.'
    totals = naming.counts(rows)
    group_type = request.GET.get('group_type', '')
    query, status, policy_id = request.GET.get('q', '').strip(), request.GET.get('status', ''), request.GET.get('policy', '')
    owner_filter = request.GET.get('owner', '')
    filtered = [r for r in rows if (not query or query.casefold() in (r['name']+' '+r['identity']+' '+str(r['observed_tags'])).casefold())
                and (not group_type or r['group_type'] == group_type)
                and (not status or r['status'] == status) and (not policy_id or r['policy'] and str(r['policy'].pk) == policy_id)
                and (not owner_filter or r['proposal'] and str(r['proposal'].owner_id) == owner_filter)]
    if request.GET.get('export') == 'csv': return csv_response(filtered)
    page = Paginator(filtered, 50).get_page(request.GET.get('page'))
    params = request.GET.copy();params.pop('page', None);params.pop('export', None)
    return render(request, 'inventory/naming_compliance.html', {'environment': environment, 'snapshot': snapshot, 'page': page,
        'group_type': group_type, 'warning': warning, 'totals': totals, 'error': error, 'query': query, 'status': status, 'policy_id': policy_id,
        'proposal_form': proposal_form, 'simulation': simulation, 'owner_filter': owner_filter,
        'owners': get_user_model().objects.filter(is_active=True, is_staff=True).order_by('username'),
        'policies': NamingPolicy.objects.filter(enabled=True), 'query_string': params.urlencode()})


@login_required
@require_http_methods(['GET'])
def suggestions(request):
    from .naming_catalog import catalog
    environment_id = request.GET.get('environment', '')
    environments = Environment.objects.order_by('name')
    if environment_id:
        if not environment_id.isdigit(): return JsonResponse({'error': 'Invalid environment.'}, status=400)
        environments = environments.filter(pk=environment_id)
    wanted_scope = request.GET.get('scope', '')
    query = request.GET.get('q', '').casefold().strip()
    results, sources, warnings = {}, [], []
    for env in environments:
        snapshot = env.snapshots.filter(testing=False, imported=False).only('pk', 'generated_at', 'browsing_status', 'summary').first()
        sources.append({'id': env.pk, 'name': env.name, 'collected_at': snapshot.generated_at.isoformat() if snapshot else None})
        if not snapshot or snapshot.browsing_status != 'ready':
            warnings.append(env.name + ': no ready full collection; manual entry remains available.')
            continue
        if snapshot.summary.get('inventory_warnings'):
            warnings.append(env.name + ': collection coverage has warnings; suggestions may be incomplete.')
        evidence = catalog(snapshot)
        if evidence['truncated']: warnings.append(env.name + ': suggestions limited to 10,000 scope/value pairs.')
        if wanted_scope:
            values = [p for p in evidence['pairs'] if p['scope'] == wanted_scope]
        else:
            values = [{'value': scope, 'groups': evidence['scope_groups'].get(scope, 0), 'vms': evidence['scope_vms'].get(scope, 0)}
                      for scope in set(evidence['scope_groups']) | set(evidence['scope_vms']) if scope]
        for item in values:
            if query not in item['value'].casefold(): continue
            result = results.setdefault(item['value'], {'value': item['value'], 'groups': 0, 'vms': 0, 'environments': []})
            result['groups'] += item['groups']; result['vms'] += item['vms']
            result['environments'].append({'name': env.name, 'groups': item['groups'], 'vms': item['vms']})
    ordered = sorted(results.values(), key=lambda r: (not bool(r['groups']), r['value'].casefold()))
    for result in ordered:
        present = {e['name'] for e in result['environments']}
        result['missing_environments'] = [env['name'] for env in sources if env['name'] not in present]
    return JsonResponse({'results': ordered[:50], 'total': len(ordered), 'sources': sources, 'warnings': warnings})


@login_required
@require_http_methods(['POST'])
def naming_example(request):
    from types import SimpleNamespace
    if not request.user.is_superuser: return HttpResponseForbidden('Administrator access is required.')
    form, components = PolicyForm(request.POST), ComponentSet(request.POST, prefix='components')
    if not form.is_valid() or not components.is_valid():
        return JsonResponse({'message': 'Complete the required policy and component fields to see an example.'})
    config = configuration(form, components)
    if not form.is_valid(): return JsonResponse({'message': 'Choose a valid category field/value pair.'})
    policy = NamingPolicy.objects.filter(pk=request.POST.get('policy_id')).first() if request.POST.get('policy_id', '').isdigit() else None
    environment = form.cleaned_data['environment'] or Environment.objects.order_by('name').first()
    if not environment: return JsonResponse({'message': 'Add an environment and collect inventory to see an example.'})
    snapshot = environment.snapshots.filter(testing=False, imported=False).only('pk', 'summary', 'browsing_status').first()
    if not snapshot or snapshot.browsing_status != 'ready':
        return JsonResponse({'message': 'No ready full collection. Manual entry and saving drafts remain available.'})
    warning = 'Collection coverage has warnings; this example is provisional.' if snapshot.summary.get('inventory_warnings') else ''
    groups = [dict(name=r['name'], identity=r['data__path'] or '', **naming.group_evidence(r['data__configuration']))
              for r in SnapshotRecord.objects.filter(snapshot=snapshot, view='inventory', data__kind='group').order_by('ordinal').values('name', 'data__path', 'data__configuration')[:100]]
    replacement = SimpleNamespace(pk=policy.pk if policy else 0, configuration=config, environment_id=environment.pk, name='Draft example')
    binding = NamingScopeBinding.objects.filter(policy=policy, environment=environment).first() if policy else None
    bindings = {replacement.pk: binding.draft_scopes if binding else {}}
    evaluated = [naming.evaluate(group, [replacement], bindings) for group in groups if config['group_type'] in ('all', group['group_type'])]
    row = next((r for r in evaluated if r['proposed']), next(iter(evaluated), None))
    if not row: return JsonResponse({'message': warning or 'No matching group in the first 100 saved groups. Save the draft and use the full impact preview.'})
    return JsonResponse({'name': row['name'], 'environment': environment.name, 'proposed': row['proposed'], 'status': row['status'],
                         'components': row['components'], 'warning': warning, 'message': 'Illustrative sample from up to 100 saved groups only. Full impact preview checks all groups, policies and name collisions before activation.'})
