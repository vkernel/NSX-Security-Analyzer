"""Human-readable saved and live evidence, kept separate from approval authority."""
import json
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from .models import Environment, Finding, FindingLiveCheck, SnapshotRecord

SUPPORTED = {'unused', 'empty_group', 'unused_service', 'zero_hits', 'disabled', 'empty_policy'}
LIMIT = 2000


def display(value):
    if value is None: return 'Unavailable'
    if value is True: return 'Yes'
    if value is False: return 'No'
    if isinstance(value, dict):
        if value.get('l4_protocol'):
            return '{} · destination ports: {} · source ports: {}'.format((value.get('display_name', '') + ' · ' + value['l4_protocol']).strip(' ·'), ', '.join(value.get('destination_ports') or ['Any']), ', '.join(value.get('source_ports') or ['Any']))
        if value.get('resource_type') == 'NestedServiceServiceEntry':
            return 'Nested service: ' + str(value.get('nested_service_path', 'Unavailable'))
        if value.get('resource_type') == 'Condition':
            return '{} · {} {} {}'.format(value.get('member_type',''),value.get('key',''),value.get('operator',''),value.get('value',''))
        if value.get('resource_type') == 'ConjunctionOperator': return value.get('conjunction_operator','Unknown conjunction')
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list): return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def differences(before, after, prefix=''):
    rows = []
    for key in sorted(set(before) | set(after)):
        if key == 'statistics_source': continue
        old, new = before.get(key), after.get(key)
        if old == new: continue
        label = prefix + key.replace('_', ' ')
        if isinstance(old, dict) and isinstance(new, dict):
            rows.extend(differences(old, new, label + ' / '))
        else:
            rows.append({'field': label, 'before': display(old)[:1000], 'after': display(new)[:1000]})
    return rows[:50]


def saved(finding):
    row = dict(finding.evidence)
    if finding.snapshot_id:
        extra = SnapshotRecord.objects.filter(snapshot_id=finding.snapshot_id,
            data__path=finding.path).values_list('data', flat=True).first()
        if extra: row = {**extra, **row}
    config = row.get('configuration') or {}
    rule = config.get('rule', config)
    facts, sections = [], []
    def fact(label, value): facts.append((label, display(value)))
    def section(key, title, values):
        sections.append({'key': key, 'title': title, 'available': values is not None, 'items': [display(v) for v in (values or [])]})
    if finding.kind in ('unused', 'empty_group'):
        status = {'empty': 'Confirmed empty', 'nonempty': 'Has members', 'non_empty': 'Has members'}.get(row.get('membership'), 'Membership unavailable')
        fact('Membership', status)
        fact('Resolved member counts', '0 — confirmed empty' if row.get('membership') == 'empty' else 'Not retained in saved evidence; use Verify current state')
        section('definition', 'Membership definition (configured criteria, not resolved members)', config.get('expression') or (row.get('membership_definition') or {}).get('definition', {}).get('expression'))
        note = 'An unused group can contain members. Unused means no references were found within the collected scope.' if finding.kind == 'unused' else 'Empty membership does not mean the group is unreferenced.'
    elif finding.kind == 'unused_service':
        fact('Object type', 'Custom service')
        fact('Service entries', len(config['service_entries']) if isinstance(config.get('service_entries'), list) else None)
        section('entries', 'Service entries — protocols, ports and nested services', config.get('service_entries'))
        note = 'These are configured services and ports, not observed network traffic.'
    elif finding.kind in ('disabled', 'zero_hits'):
        for key, label in [('action','Action'), ('disabled','Disabled'), ('sequence_number','Position')]: fact(label, rule.get(key, row.get(key)))
        fact('Parent policy', finding.path.rsplit('/rules/', 1)[0])
        for key, label in [('source_groups','Sources'), ('destination_groups','Destinations'), ('services','Services'), ('scope','Applied to')]: section(key, label, rule.get(key, row.get(key)))
        fact('Recorded hits', row.get('hit_count'))
        fact('Statistics checked', row.get('statistics_checked_at'))
        section('parent', 'Parent policy settings', [config['policy']] if config.get('policy') else [])
        note = 'Missing counters are unavailable, not zero. Rule configuration alone does not establish effective traffic behavior.'
    else:
        fact('Rule count', row.get('rule_count'))
        for key, label in [('category','Category'), ('sequence_number','Position')]: fact(label, config.get(key))
        section('scope', 'Applied to', config.get('scope'))
        rules = list(SnapshotRecord.objects.filter(snapshot_id=finding.snapshot_id, view='dfw', data__path__startswith=finding.path+'/rules/').values_list('data', flat=True)) if finding.snapshot_id else []
        # Older snapshots can use a different view name; path is authoritative.
        if not rules and finding.snapshot_id:
            rules = list(SnapshotRecord.objects.filter(snapshot_id=finding.snapshot_id, data__path__startswith=finding.path+'/rules/').values_list('compact', flat=True))
        fact('Enabled / disabled rules', f"{sum(not r.get('disabled', False) for r in rules)} / {sum(bool(r.get('disabled')) for r in rules)}" if rules or row.get('rule_count') == 0 else None)
        section('rules', 'Contained firewall rules', rules if rules or row.get('rule_count') == 0 else None)
        note = 'An empty policy requires a complete rule inventory with zero rules, including disabled rules.'
    section('references', 'Referencing rules and parent objects', row.get('referenced_by'))
    baseline = finding.approvals.get('owner', {}).get('evidence')
    if baseline is None:
        event = finding.events.filter(details__action='approve', details__from='owner_review', details__decision__evidence__isnull=False).order_by('-created_at', '-pk').first()
        if event: baseline = event.details.get('decision', {}).get('evidence')
    changes = differences(baseline, finding.evidence) if baseline is not None else []
    return {'facts': facts, 'sections': sections, 'note': note, 'changes': changes,
            'has_baseline': baseline is not None, 'collected_at': finding.last_seen,
            'snapshot_retained': bool(finding.snapshot_id)}


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def live(request, pk, finding_id):
    finding = get_object_or_404(Finding, pk=finding_id, environment_id=pk)
    if not request.user.is_staff: return HttpResponseForbidden('Operator access is required.')
    if finding.kind not in SUPPORTED: return JsonResponse({'error': 'This finding type is not supported.'}, status=400)
    if request.method == 'POST':
        with transaction.atomic():
            Environment.objects.select_for_update().get(pk=pk)
            finding = Finding.objects.select_for_update().get(pk=finding_id)
            check = FindingLiveCheck.objects.filter(finding=finding).first()
            if check and check.status in ('queued', 'running'):
                return JsonResponse({'error': 'A check is already queued or running.'}, status=409)
            check, _ = FindingLiveCheck.objects.update_or_create(finding=finding, defaults={
                'requested_by': request.user, 'status': 'queued', 'requested_at': timezone.now(), 'finished_at': None,
                'snapshot_id_text': str(finding.snapshot_id or ''), 'revision': finding.revision,
                'progress': 0, 'message': 'Queued for read-only verification.', 'result': {}})
            from .audit_events import record
            record('finding.verification_requested', 'Finding', finding.pk)
    else:
        check = FindingLiveCheck.objects.filter(finding=finding).first()
    if not check: return JsonResponse({'status': 'none', 'message': 'No current-state check requested.'})
    result = dict(check.result)
    section = request.GET.get('section', '')
    if section:
        items = result.get('lists', {}).get(section, [])
        query = request.GET.get('q', '')[:200].casefold()
        items = [v for v in items if query in display(v).casefold()]
        try: page = max(0, int(request.GET.get('page', '0')))
        except ValueError: return JsonResponse({'error':'Invalid page.'}, status=400)
        return JsonResponse({'items': items[page*25:(page+1)*25], 'count': len(items), 'page': page})
    result.pop('lists', None)
    return JsonResponse({'status': check.status, 'progress': check.progress, 'message': check.message,
        'finished_at': check.finished_at, 'requested_at': check.requested_at, 'result': result,
        'stale': check.snapshot_id_text != str(finding.snapshot_id or '')})
