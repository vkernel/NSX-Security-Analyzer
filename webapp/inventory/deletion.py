"""Explicit, admin-confirmed deletion. No force, redirect, cascade or DELETE retry."""
from contextlib import contextmanager
from datetime import timedelta
import json
import re
from urllib.parse import quote
import urllib3
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q, Prefetch, Case, When, Value, BooleanField
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from . import collector
from .credentials import decrypt_password
from .models import DeletionBatch, DeletionItem, Environment, Finding
from .finding_workflow import approval_readiness, review_fingerprint, coverage_changed, event
from .audit_events import record

KINDS = {'unused', 'unused_service', 'empty_group', 'zero_hits', 'disabled', 'empty_policy'}
PATTERNS = {
    'Group': r'/infra/domains/[^/]+/groups/[^/]+',
    'Service': r'/infra/services/[^/]+',
    'Rule': r'/infra/domains/[^/]+/security-policies/[^/]+/rules/[^/]+',
    'SecurityPolicy': r'/infra/domains/[^/]+/security-policies/[^/]+',
}


def permitted(user):
    return bool(user and user.is_active and user.is_superuser and user.has_perm('inventory.execute_nsx_deletion'))


def target_type(path):
    if any(part in ('.', '..') for part in path.split('/')) or any(c in path for c in '%?#\\\r\n'):
        raise ValidationError('Unsupported or unsafe NSX object path.')
    for kind, pattern in PATTERNS.items():
        if re.fullmatch(pattern, path): return kind
    raise ValidationError('Only local-manager groups, custom services, DFW rules and empty DFW policies are supported.')


def clean(value):
    if isinstance(value, dict): return {k: clean(v) for k, v in value.items() if not k.startswith('_')}
    if isinstance(value, list): return [clean(v) for v in value]
    return value


@contextmanager
def connection(env):
    if not env.enabled: raise ValidationError('The environment is paused.')
    if not env.username or not env.password_ciphertext: raise ValidationError('Environment credentials are missing.')
    client = collector.NSXClient(env.manager, env.username, decrypt_password(env.password_ciphertext),
        timeout=min(env.timeout, 30), retries=0, insecure=env.insecure,
        ca_data=env.ca_certificate or None, ca_bundle=None if env.ca_certificate else env.ca_bundle or None)
    try: yield client
    finally: client.close()


def approvals_valid(finding):
    if finding.kind not in KINDS or finding.workflow_state != 'ready' or not finding.owner_id:
        raise ValidationError('The finding must be assigned and ready for decommissioning.')
    readiness = approval_readiness(finding, deletion_live=True)
    if readiness['status'] == 'blocked': raise ValidationError(readiness['message'])
    stages = ['owner', 'second'] if finding.required_approvals == 2 else ['owner']
    if finding.required_approvals not in (1, 2): raise ValidationError('Invalid approval requirement.')
    digest = review_fingerprint(finding.kind, finding.evidence)
    decisions = [finding.approvals.get(stage, {}) for stage in stages]
    if any(not d.get('actor_id') or d.get('fingerprint') != digest for d in decisions):
        raise ValidationError('Current evidence does not have all required approvals.')
    if decisions[0]['actor_id'] != str(finding.owner_id): raise ValidationError('Owner approval does not match the current owner.')
    if len(decisions) == 2 and decisions[0]['actor_id'] == decisions[1]['actor_id']:
        raise ValidationError('Four-eyes requires two different reviewers.')
    if coverage_changed(finding, readiness.get('coverage')): raise ValidationError('Approved reference coverage changed.')
    if readiness['status'] == 'manual' and any(not d.get('manual_verified') for d in decisions):
        raise ValidationError('Independent verification is missing from an approval.')
    return readiness


def current_object(client, path):
    try: return client.get(path)
    except collector.AuditError as exc:
        if exc.status_code == 404: return None
        raise


def validate_live(client, finding):
    """Read-only preflight, repeated for each target immediately before execution."""
    client.direct_membership_verified = False
    client.cleanup_plan = []
    readiness = approvals_valid(finding)
    kind = target_type(finding.path)
    expected_type = {'unused':'Group', 'empty_group':'Group', 'unused_service':'Service', 'zero_hits':'Rule', 'disabled':'Rule', 'empty_policy':'SecurityPolicy'}[finding.kind]
    if kind != expected_type: raise ValidationError('Finding type does not match the target path.')
    obj = current_object(client, finding.path)
    if obj is None: return None
    if obj.get('resource_type') != kind or obj.get('path') != finding.path:
        raise ValidationError('NSX returned a different object identity.')
    if obj.get('_system_owned') or obj.get('is_default') or obj.get('_protection') not in (None, 'NOT_PROTECTED') or obj.get('marked_for_delete'):
        raise ValidationError('Protected, default, system-owned or already deleting objects cannot be deleted here.')
    evidence = finding.evidence
    if not evidence.get('unique_id') or evidence.get('created_at') is None:
        raise ValidationError('Approved object identity is incomplete. Collect and approve fresh evidence.')
    if obj.get('unique_id') != evidence['unique_id'] or obj.get('_create_time') != evidence['created_at']:
        raise ValidationError('Object identity changed since approval.')
    configuration = clean(obj)
    if kind == 'Rule':
        parent = client.get(finding.path.rsplit('/rules/', 1)[0])
        if parent.get('_system_owned') or parent.get('is_default') or parent.get('_protection') not in (None, 'NOT_PROTECTED'): raise ValidationError('Rules in system policies are excluded.')
        configuration = {'rule': configuration, 'policy': {k: parent.get(k) for k in ('scope','category','sequence_number','stateful')}}
    if not evidence.get('configuration') or configuration != clean(evidence['configuration']):
        raise ValidationError('Configuration differs from approved evidence. Collect again and repeat review.')
    domains = collector.objects(client, '/infra/domains')
    groups = [g for d in domains for g in collector.objects(client, d['path'] + '/groups')]
    services = collector.objects(client, '/infra/services')
    resources, coverage = collector.verified_configuration_search(client, groups, services)
    client.search_diagnostics = coverage
    if coverage.get('mode') != 'all_types' or coverage.get('errors') or coverage.get('failed'):
        raise ValidationError('Live dependency coverage is incomplete. Automated deletion is blocked; manual verification cannot override this.')
    if not any(r.get('path') == finding.path for r in resources):
        raise ValidationError('The live search index does not contain the target object.')
    refs = collector.collect_references(resources + groups + services, [obj])[finding.path]
    cleanup_allowed = finding.kind == 'empty_group' and getattr(client, 'allow_reference_cleanup', False) is True
    if refs and not cleanup_allowed:
        raise ValidationError('Referenced by: ' + ', '.join(sorted(refs)[:10]))
    children = [r['path'] for r in resources if r.get('path', '').startswith(finding.path + '/')]
    if kind != 'Service' and children: raise ValidationError('The object has child configuration; cascading deletion is blocked.')
    if kind == 'Service' and any(not p.startswith(finding.path + '/service-entries/') for p in children):
        raise ValidationError('Unsupported service child configuration exists.')
    if kind == 'SecurityPolicy' and collector.objects(client, finding.path + '/rules'):
        raise ValidationError('The firewall policy is no longer empty.')
    if finding.kind == 'empty_group':
        if isinstance(readiness, dict) and readiness.get('requires_direct_membership'):
            from .deletion_readiness import strict_group_empty
            if not strict_group_empty(client, obj):
                raise ValidationError('Direct membership verification did not return explicit zero VM, IP and port members. Deletion remains blocked.')
            client.direct_membership_verified = True
        else:
            state, _ = collector.membership(client, obj, group_inventory={g['path']:g for g in groups})
            if state != 'empty': raise ValidationError('Live group membership is not confirmed empty.')
    if finding.kind == 'disabled' and obj.get('disabled') is not True:
        raise ValidationError('The rule is no longer disabled.')
    if finding.kind == 'zero_hits':
        statistics = collector.rule_statistics(client, obj)
        if statistics.get('hit_status') != 'zero_hits' or statistics.get('hit_count') != 0:
            raise ValidationError('Fresh rule statistics do not confirm zero hits.')
    if getattr(client, 'inventory_warnings', []):
        raise ValidationError('Live inventory changed during verification; deletion is blocked.')
    if refs:
        from .reference_cleanup import build_plan
        client.cleanup_plan = build_plan(client, obj, refs, resources + groups + services)
    return obj


def delete_once(client, path):
    kind = target_type(path)
    url = client.base_url + quote(path, safe='/')
    if kind == 'Group': url += '?fail_if_subtree_exists=true'
    response = client.http.request('DELETE', url, headers=client.headers, retries=False, redirect=False,
        timeout=urllib3.Timeout(connect=client.timeout, read=client.timeout), pool_timeout=client.timeout)
    collector.record_access_failure(client, 'DELETE', path, response.status)
    return response.status


def describe_error(exc):
    if isinstance(exc, ValidationError): return '; '.join(exc.messages)
    if isinstance(exc, collector.InventoryChanged):
        return 'Live inventory verification failed: ' + str(exc)[:2000] + '. Review live dependency-search diagnostics. No DELETE retry was performed.'
    if isinstance(exc, collector.AuditError) and exc.status_code in (401, 403):
        return collector.access_message(getattr(exc, 'method', None), getattr(exc, 'path', None), exc.status_code)
    if isinstance(exc, collector.AuditError): return 'NSX verification failed (HTTP {}). No DELETE retry was performed.'.format(exc.status_code or 'unavailable')
    return 'Operation interrupted or failed. Check NSX and the audit record before starting another deletion.'


def guard(batch, finding, item):
    if batch.requested_by_id:
        batch.requested_by.refresh_from_db()
    if not permitted(batch.requested_by): raise ValidationError('The requesting administrator is no longer authorized.')
    if batch.environment.manager != batch.manager: raise ValidationError('The configured NSX Manager changed. Create a new preview.')
    if not finding or finding.environment_id != batch.environment_id or finding.path != item.path or finding.revision != item.revision:
        raise ValidationError('The finding changed. Create a new preview.')
    approvals_valid(finding)


def execution_authorized(batch, item):
    """Confirmation is timely; execution has its own bounded authorization."""
    if not batch.confirmed_at or not batch.ticket:
        raise ValidationError('Explicit confirmation and a change ticket are required.')
    if (not item.verified_at or batch.confirmed_at < item.verified_at
            or batch.confirmed_at - item.verified_at > timedelta(minutes=10)):
        raise ValidationError('Preview expired before confirmation. Create a new preview.')
    if timezone.now() - batch.confirmed_at > timedelta(hours=1):
        raise ValidationError('Execution authorization expired after one hour. Create a new preview and confirm again.')


def process_item(item, execute=False):
    batch = item.batch
    if execute and not DeletionItem.objects.filter(pk=item.pk, status='verified').update(status='validating'):
        return
    sent = False
    client = None
    try:
        finding = item.finding
        guard(batch, finding, item)
        if execute:
            execution_authorized(batch, item)
        with connection(batch.environment) as client:
            client.allow_reference_cleanup = finding.kind == 'empty_group'
            obj = validate_live(client, finding)
            if obj is None:
                item.status, item.message = 'already_absent', 'Already absent in NSX; no deletion sent and no review marked decommissioned.'
            elif not execute:
                item.before, item.approved_evidence, item.approvals = obj, finding.evidence, finding.approvals
                item.verified_at = timezone.now()
                item.verification = {
                    'Membership verification': 'Explicit zero VM, IP and port members; retained qualified history' if getattr(client, 'direct_membership_verified', False) is True else 'Standard finding-specific live checks',
                    'Identity and configuration': 'Matches approved evidence',
                    'Approvals and history': 'Current required approvals and observation criteria verified',
                    'Dependencies': 'Fresh unrestricted reference search passed; no incoming references',
                    'Finding condition': {'empty_group':'Fresh group membership confirmed empty',
                                  'unused':'No configuration references found',
                                  'unused_service':'No configuration references found',
                                  'disabled':'Rule remains disabled', 'zero_hits':'Fresh zero counters confirmed',
                                  'empty_policy':'Fresh rule inventory confirmed empty'}[finding.kind],
                }
                from .related_decommission import related_findings
                item.verification['Related findings'] = [
                    {'id': row.pk, 'kind': {'unused': 'Unused group', 'unused_service': 'Unused custom service'}.get(row.kind, row.kind.replace('_', ' ').capitalize()), 'name': row.name}
                    for row in related_findings(finding)]
                item.status, item.message = 'verified', 'Ready for confirmation. Required live checks passed; explicit execution authorization is still required.'
                plan = getattr(client, 'cleanup_plan', None)
                if isinstance(plan, list) and plan:
                    deleted_paths = [row['path'] for row in plan if row.get('action') == 'delete_rule']
                    if batch.items.filter(path__in=deleted_paths).exists():
                        raise ValidationError('A dependent rule is also selected in this batch. Preview the group separately to review the combined deletion.')
                    item.cleanup_plan, item.cleanup_approvals = plan, []
                    item.status = 'cleanup_review'
                    item.message = f'Reference cleanup required — {len(plan)} firewall rule(s). Review and approve the exact edits before confirming deletion.'
                    item.verification['Dependencies'] = 'References found; separate firewall-change approval required'

            else:
                if obj != item.before: raise ValidationError('Object changed after preview. Create a new preview.')
                from .reference_cleanup import validate_approvals, apply_plan
                plan = getattr(client, 'cleanup_plan', None)
                if item.cleanup_plan:
                    if plan != item.cleanup_plan:
                        raise ValidationError('References or rule configuration changed after preview. Create a new preview.')
                    validate_approvals(item)
                    def authorize_cleanup():
                        fresh = Finding.objects.get(pk=item.finding_id)
                        batch.environment.refresh_from_db()
                        guard(batch, fresh, item)
                        item.finding = fresh
                        validate_approvals(item)
                        execution_authorized(batch, item)
                        if current_object(client, item.path) != obj:
                            raise ValidationError('Group changed before reference cleanup.')
                        if not batch.environment.enabled:
                            raise ValidationError('The environment is paused.')
                        # Membership can change without a group revision change.
                        readiness = approvals_valid(fresh)
                        if isinstance(readiness, dict) and readiness.get('requires_direct_membership'):
                            from .deletion_readiness import strict_group_empty
                            empty = strict_group_empty(client, obj)
                        else:
                            domains = collector.objects(client, '/infra/domains')
                            groups = [g for domain in domains for g in collector.objects(client, domain['path'] + '/groups')]
                            state, _ = collector.membership(client, obj, group_inventory={g['path']:g for g in groups})
                            empty = state == 'empty'
                        if not empty or getattr(client, 'inventory_warnings', []):
                            raise ValidationError('Group is no longer verified empty; reference cleanup stopped.')
                    apply_plan(client, item, authorize_cleanup)
                    client.allow_reference_cleanup = False
                    if validate_live(client, finding) != obj:
                        raise ValidationError('Group changed after reference cleanup; deletion stopped.')
                elif isinstance(plan, list) and plan:
                    raise ValidationError('New references require a separately approved cleanup plan.')
                # Commit durable intent before the network mutation. A crash never causes an automatic retry.
                item.status = 'sending'
                item.request_sent_at = timezone.now()
                item.save(update_fields=['status','request_sent_at'])
                with transaction.atomic():
                    env = Environment.objects.select_for_update().get(pk=batch.environment_id)
                    finding = Finding.objects.select_for_update().get(pk=item.finding_id)
                    batch.environment = env
                    guard(batch, finding, item)
                    if not env.enabled: raise ValidationError('The environment is paused.')
                    execution_authorized(batch, item)
                    if target_type(item.path) == 'Rule':
                        parent = client.get(item.path.rsplit('/rules/', 1)[0])
                        if {k: parent.get(k) for k in ('scope','category','sequence_number','stateful')} != finding.evidence['configuration']['policy']:
                            raise ValidationError('Parent policy changed immediately before deletion.')
                    if current_object(client, item.path) != obj:
                        raise ValidationError('Object changed immediately before deletion.')
                    execution_authorized(batch, item)
                    if item.cleanup_plan:
                        item.finding = finding
                        validate_approvals(item)
                    sent = True
                    status = delete_once(client, item.path)
                    item.http_status = status
                    if status not in (200, 202, 204):
                        item.status = 'failed' if status in (400, 401, 403, 405, 409, 412, 429) else 'uncertain'
                        item.message = (collector.access_message('DELETE', item.path, status) if status in (401, 403)
                                        else f'DELETE returned HTTP {status}; verify NSX before a new request.') + ' No retry performed.'
                    elif current_object(client, item.path) is not None:
                        item.status, item.message = 'uncertain', 'DELETE accepted, but absence is not verified. Check NSX; do not blindly retry.'
                    else:
                        before = finding.workflow_state
                        finding.workflow_state = 'decommissioned'
                        finding.present = False
                        finding.change_ticket = batch.ticket
                        finding.revision += 1
                        finding.save(update_fields=['workflow_state','change_ticket','revision','present'])
                        event(finding, 'nsx_deleted', before, 'NSX DELETE accepted and object absence verified.', batch.requested_by,
                              {'deletion_batch':str(batch.pk), 'deletion_item':item.pk, 'change_ticket':batch.ticket, 'http_status':status})
                        from .related_decommission import close_related
                        close_related(finding, batch.requested_by, deletion_item=item)
                        item.status, item.message = 'deleted', 'Deleted from NSX and absence verified.'
                        # Commit deletion evidence with review closure under the
                        # environment lock, before publication can proceed.
                        item.finished_at = timezone.now()
                        item.save()
    except Exception as exc:
        item.status = 'uncertain' if sent else 'blocked'
        item.message = describe_error(exc)
        if item.cleanup_results:
            updated = sum(row.get('status') == 'updated' for row in item.cleanup_results)
            deleted = sum(row.get('status') == 'deleted' for row in item.cleanup_results)
            if any(row.get('status') in ('sending', 'uncertain') for row in item.cleanup_results):
                item.status = 'uncertain'
            item.message += f' Reference cleanup: {updated} rule update(s) verified; {deleted} rule deletion(s) verified. Review each recorded outcome; no automatic rollback or retry.'
    from .nsx_access import audit_failures
    access_failures = audit_failures(client, batch.environment_id, item=item)
    if access_failures:
        item.verification = {**item.verification, 'access_failures': access_failures}
    diagnostics = getattr(client, 'search_diagnostics', None)
    if isinstance(diagnostics, dict):
        # Persist only search metadata, never result objects, headers or credentials.
        allowed = ('query', 'mode', 'status', 'runtime_filtering', 'resource_types',
                   'excluded_resource_types', 'rejected_queries', 'inventory_retries',
                   'inventory_coverage_retries', 'search_scans', 'raw_records',
                   'configuration_records', 'runtime_records_removed', 'missing_groups',
                   'missing_services', 'error', 'http_status', 'pagination_attempts')
        item.verification = {**item.verification,
            'search_diagnostics': {key: diagnostics[key] for key in allowed if key in diagnostics}}
    item.finished_at = timezone.now()
    item.save()
    record('nsx.deletion_' + item.status, 'DeletionItem', item.pk, actor=batch.requested_by_id or '',
           details={'batch':str(batch.pk), 'name':item.name, 'path':item.path, 'environment_id':batch.environment_id,
                    'ticket':batch.ticket, 'http_status':item.http_status, 'message':item.message})


def claim(*, preview=False):
    with transaction.atomic():
        batch = DeletionBatch.objects.select_for_update(skip_locked=True).filter(status='preview_queued' if preview else 'queued').order_by('created_at').first()
        if batch:
            batch.status = 'checking' if batch.status == 'preview_queued' else 'running'
            batch.save(update_fields=['status'])
        return batch


def run(pk):
    batch = DeletionBatch.objects.select_related('environment','requested_by').get(pk=pk)
    if batch.status not in ('checking','running'): return
    execute = batch.status == 'running'
    record('nsx.deletion_execution_started' if execute else 'nsx.deletion_preview_started',
           'DeletionBatch', batch.pk, actor='', details={'environment_id':batch.environment_id, 'ticket':batch.ticket})
    for item in batch.items.select_related('finding').order_by('pk'):
        item.batch = batch
        if execute and item.status != 'verified': continue
        process_item(item, execute)
    batch.status = 'completed' if execute else 'preview_ready'
    if execute: batch.finished_at = timezone.now()
    else: batch.checked_at = timezone.now()
    batch.save()
    record('nsx.deletion_execution_finished' if execute else 'nsx.deletion_preview_finished',
           'DeletionBatch', batch.pk, actor='', details={'environment_id':batch.environment_id, 'ticket':batch.ticket,
           'message':'Processing finished; individual object outcomes determine whether deletion succeeded.'})


def interrupted(pk):
    batch = DeletionBatch.objects.filter(pk=pk, status__in=['checking','running']).first()
    if not batch: return
    batch.items.filter(status='sending').update(status='uncertain', message='Worker stopped after deletion intent was recorded. Verify NSX before retrying.')
    batch.items.filter(status__in=['pending','verified','validating','cleanup_review']).update(status='blocked', message='Worker interrupted; create a new preview.')
    batch.items.exclude(cleanup_results=[]).exclude(status='deleted').update(status='uncertain', message='Worker interrupted after reference cleanup began. Review per-rule outcomes in NSX before a new preview. No automatic retry.')
    batch.status = 'interrupted'; batch.finished_at = timezone.now(); batch.save()


@login_required
@require_GET
def history(request, pk):
    if not permitted(request.user):
        return HttpResponseForbidden('Administrator deletion permission is required.')
    env = get_object_or_404(Environment, pk=pk)
    query = request.GET.get('q', '').strip()[:255]
    outcome = request.GET.get('outcome', '')
    object_type = request.GET.get('type', '')
    outcomes = {'deleted': 'Deleted', 'blocked': 'Blocked', 'already_absent': 'Already absent',
                'failed': 'Failed', 'uncertain': 'Uncertain', 'verified': 'Verified',
                'pending': 'Pending', 'cleanup_review': 'Reference cleanup required', 'validating': 'Rechecking live evidence', 'sending': 'Deleting'}
    types = {'Group': 'Group', 'Service': 'Custom service', 'Rule': 'Firewall rule', 'SecurityPolicy': 'Security policy'}
    matches = DeletionItem.objects.filter(batch__environment=env)
    if query:
        matches = matches.filter(Q(name__icontains=query) | Q(path__icontains=query) | Q(batch__ticket__icontains=query))
    if outcome:
        matches = matches.filter(status=outcome) if outcome in outcomes else matches.none()
    if object_type:
        matches = matches.filter(path__regex='^' + PATTERNS[object_type] + '$') if object_type in types else matches.none()
    batches = DeletionBatch.objects.filter(environment=env)
    if query or outcome or object_type:
        batches = batches.filter(pk__in=matches.values('batch_id'))
    batches = batches.select_related('requested_by').annotate(
        object_count=Count('items'), deleted_count=Count('items', filter=Q(items__status='deleted')),
        blocked_count=Count('items', filter=Q(items__status='blocked')),
        attention_count=Count('items', filter=Q(items__status__in=['failed', 'uncertain'])),
    ).prefetch_related(Prefetch('items', queryset=DeletionItem.objects.annotate(has_configuration=Case(When(before={}, then=Value(False)), default=Value(True), output_field=BooleanField()), has_cleanup=Case(When(cleanup_plan=[], then=Value(False)), default=Value(True), output_field=BooleanField())).defer('before', 'approved_evidence', 'approvals', 'verification', 'cleanup_plan', 'cleanup_approvals', 'cleanup_results').order_by('pk'), to_attr='history_items')).order_by('-created_at')
    page = Paginator(batches, 25).get_page(request.GET.get('page'))
    for batch in page:
        for item in batch.history_items:
            try:
                item.type_label = types[target_type(item.path)]
            except ValidationError:
                item.type_label = 'Object'
            item.outcome_label = outcomes.get(item.status, item.status.replace('_', ' ').capitalize())
        batch.preview_items = batch.history_items[:3]
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'inventory/deletion_history.html', {
        'environment': env, 'page': page, 'query': query, 'outcome': outcome, 'object_type': object_type,
        'outcomes': outcomes, 'object_types': types, 'filters_active': bool(query or outcome or object_type),
        'query_string': params.urlencode(),
    })


@login_required
@require_http_methods(['GET','POST'])
def selection(request, pk):
    if not permitted(request.user): return HttpResponseForbidden('Administrator deletion permission is required.')
    env = get_object_or_404(Environment, pk=pk)
    error = ''
    if request.method == 'POST':
        ids = request.POST.getlist('finding')
        revisions = {}
        selected = request.POST.getlist('selected')
        if selected:
            try:
                pairs = [value.split(':') for value in selected]
                if len(pairs) > 100 or any(len(pair) != 2 or not all(v.isdecimal() and len(v) < 19 for v in pair) for pair in pairs):
                    raise ValueError
                revisions = {int(pk): int(revision) for pk, revision in pairs}
                ids = [pk for pk, revision in pairs]
            except ValueError:
                ids = []
        if not ids or len(ids) > 100 or any(not v.isdecimal() for v in ids):
            error = 'Select between 1 and 100 findings.'
        else:
            with transaction.atomic():
                Environment.objects.select_for_update().get(pk=env.pk)
                rows = list(Finding.objects.filter(environment=env, pk__in=ids, workflow_state='ready', kind__in=KINDS).order_by('pk'))
                if len(rows) != len(set(ids)): error = 'Some selected findings are no longer ready. Refresh and select again.'
                elif any(row.pk in revisions and row.revision != revisions[row.pk] for row in rows):
                    error = 'Some selected findings changed. Refresh the finding reviews page and select again.'
                else:
                    batch = DeletionBatch.objects.create(environment=env, manager=env.manager, requested_by=request.user)
                    seen = set()
                    for row in rows:
                        if row.path in seen: continue
                        seen.add(row.path)
                        DeletionItem.objects.create(batch=batch, finding=row, revision=row.revision, path=row.path, name=row.name)
                    record('nsx.deletion_preview_requested','DeletionBatch',batch.pk, actor=request.user.pk, details={'environment_id':env.pk,'count':len(seen)})
                    return redirect('deletion-detail', pk=batch.pk)
    rows = env.findings.filter(workflow_state='ready',kind__in=KINDS).order_by('name')[:100]
    return render(request,'inventory/deletion_selection.html',{'environment':env,'rows':rows,'error':error,
        })


@login_required
@require_http_methods(['GET','POST'])
def detail(request, pk):
    if not permitted(request.user): return HttpResponseForbidden('Administrator deletion permission is required.')
    error = ''
    with transaction.atomic():
        batch = get_object_or_404(DeletionBatch.objects.select_for_update(of=('self',)).select_related('environment','requested_by'), pk=pk)
        if request.method == 'POST':
            ticket = request.POST.get('ticket','').strip()
            if (batch.requested_by_id != request.user.pk or batch.status != 'preview_ready' or not batch.checked_at
                or timezone.now() - batch.checked_at > timedelta(minutes=10)):
                error = 'Preview is unavailable, expired or belongs to another administrator. Create a new preview.'
            elif batch.items.filter(status='verified', verified_at__isnull=True).exists() or batch.items.filter(status='verified', verified_at__lt=timezone.now()-timedelta(minutes=10)).exists():
                error = 'One or more object previews expired. Create a new preview.'
            elif not ticket or len(ticket)>255 or request.POST.get('confirm') != 'yes':
                error = 'Provide a change ticket and explicitly confirm permanent deletion.'
            elif not batch.items.filter(status='verified').exists(): error = 'No objects passed the live checks.'
            else:
                from .reference_cleanup import validate_approvals
                cleanup_items = list(batch.items.filter(status='verified').exclude(cleanup_plan=[]).select_related('finding'))
                try:
                    if cleanup_items and request.POST.get('confirm_cleanup') != 'yes':
                        raise ValidationError('Explicitly confirm the firewall rule changes as well as deletion.')
                    for cleanup_item in cleanup_items:
                        validate_approvals(cleanup_item)
                except ValidationError as exc:
                    error = '; '.join(exc.messages)
                if not error:
                    batch.ticket, batch.status, batch.confirmed_at = ticket, 'queued', timezone.now()
                    batch.save(update_fields=['ticket','status','confirmed_at'])
                    record('nsx.deletion_confirmed','DeletionBatch',batch.pk, actor=request.user.pk,
                           details={'ticket':ticket,'environment_id':batch.environment_id,'count':batch.items.filter(status='verified').count()})
                    return redirect('deletion-detail',pk=pk)
    latest = batch.environment.snapshots.filter(testing=False, imported=False).only('pk', 'generated_at', 'summary').first()
    warnings = []
    if latest and latest.summary.get('inventory_warnings'):
        warnings = batch.environment.snapshots.filter(pk=latest.pk).values_list('report__inventory_warnings', flat=True).first() or []
    items = list(batch.items.select_related('finding'))
    from .reference_cleanup import summary as cleanup_summary
    for item in items:
        item.cleanup_summary = cleanup_summary(item.cleanup_plan) if item.cleanup_plan else ''
        item.has_configuration = bool(item.before)
        item.has_cleanup = bool(item.cleanup_plan)
        item.cleanup_parent = item.verification.get('Cleanup parent item')
        item.related_findings = item.verification.get('Related findings', [])
        item.search_diagnostics_json = json.dumps(item.verification.get('search_diagnostics', {}), indent=2)
    counts = {status: sum(item.status == status for item in items) for status in
              ('pending', 'verified', 'validating', 'sending', 'deleted', 'blocked', 'already_absent', 'failed', 'uncertain', 'cleanup_review')}
    active = batch.status in ('preview_queued', 'checking', 'queued', 'running')
    execution = bool(batch.confirmed_at)
    done = sum(counts[status] for status in ('deleted', 'blocked', 'already_absent', 'failed', 'uncertain'))
    if not execution:
        done += counts['verified'] + counts['cleanup_review']
    for item in items:
        item.display_status = {'pending': 'Waiting for verification', 'verified': 'Ready to confirm' if not execution else 'Waiting for live checks',
            'cleanup_review': 'Reference cleanup required', 'validating': 'Rechecking live evidence', 'sending': 'Deleting and verifying absence',
            'deleted': 'Deleted', 'blocked': 'Blocked', 'already_absent': 'Already absent',
            'failed': 'Deletion failed', 'uncertain': 'Outcome needs verification'}.get(item.status, item.status)
    stamps = [item.verified_at for item in items if item.status in ('verified', 'cleanup_review') and item.verified_at]
    deadline = (batch.confirmed_at + timedelta(hours=1) if execution else
                min(stamps + ([batch.checked_at] if batch.checked_at else [])) + timedelta(minutes=10) if stamps else None)
    expired = bool(deadline and timezone.now() >= deadline)
    heading = batch.status_label
    if batch.status == 'completed':
        heading = f"{counts['deleted']} deleted · {counts['blocked']} blocked"
        for key, label in [('already_absent', 'already absent'), ('failed', 'failed'), ('uncertain', 'uncertain')]:
            if counts[key]:
                heading += f" · {counts[key]} {label}"
    elapsed = int(((batch.finished_at or (batch.checked_at if not active and not execution else None) or timezone.now()) - batch.created_at).total_seconds())
    rule_deletes = sum(row.get('action') == 'delete_rule' for item in items if item.status == 'verified' for row in item.cleanup_plan)
    context = {'batch':batch, 'environment':batch.environment, 'items':items, 'counts':counts,
        'status_label':heading, 'active':active, 'execution':execution, 'done_count':done, 'total_count':len(items),
        'deadline':deadline, 'expired':expired, 'updated_at':timezone.now(),
        'elapsed_label':f'{max(0, elapsed) // 60}m {max(0, elapsed) % 60}s',
        'latest_snapshot':latest, 'inventory_warnings':warnings, 'inventory_blocked':bool(latest and latest.summary.get('inventory_warnings')),
        'error':error, 'verified_count':counts['verified'],
        'cleanup_count':sum(len(item.cleanup_plan) for item in items if item.status == 'verified'),
        'cleanup_rule_deletes':rule_deletes,
        'confirmation_object_count':counts['verified'] + rule_deletes,
        'cleanup_rule_updates':sum(row.get('action') != 'delete_rule' for item in items if item.status == 'verified' for row in item.cleanup_plan),
        'can_confirm':batch.status=='preview_ready' and batch.requested_by_id==request.user.pk and not expired}
    template = 'inventory/deletion_progress.html' if request.GET.get('fragment') == '1' and request.method == 'GET' else 'inventory/deletion_detail.html'
    response = render(request, template, context)
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_GET
def saved_configuration(request, pk):
    """Read only: the immutable saved payload, never a live NSX request."""
    if not permitted(request.user):
        return HttpResponseForbidden('Administrator access is required to view deletion configuration.')
    item = get_object_or_404(DeletionItem.objects.select_related('batch__environment', 'batch__requested_by'), pk=pk)
    payload = json.dumps(item.before, indent=2, ensure_ascii=False)
    if request.GET.get('download') == '1':
        if not item.before:
            return HttpResponse('No configuration was saved for this item.', status=404)
        response = HttpResponse(payload, content_type='application/json; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="nsx-deletion-{item.pk}-configuration.json"'
    else:
        guidance = {
            'Group': 'Recreate the group in its original domain with the saved name, tags and membership expressions. Check referenced groups and segments. Dynamic membership uses current inventory; VDI automation may change it again.',
            'Service': 'Recreate the custom service and its service entries, checking protocols and ports. Separately removed references are not restored.',
            'Rule': 'Check the original parent policy, rule order, scope, action and referenced groups/services before recreating the rule. Recreating it can change traffic behavior.',
            'SecurityPolicy': 'Check category, scope and policy ordering before recreating the empty policy. Separately deleted rules are not included.',
        }.get(item.before.get('resource_type'), 'Review the saved object type and dependencies before manual recreation.')
        response = render(request, 'inventory/deletion_configuration.html', {
            'item': item, 'configuration': payload, 'guidance': guidance,
            'expressions': json.dumps(item.before.get('expression', []), indent=2, ensure_ascii=False),
            'extended_expressions': json.dumps(item.before.get('extended_expression', []), indent=2, ensure_ascii=False),
        })
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_http_methods(['GET', 'POST'])
def reference_cleanup(request, pk):
    """Approve immutable proposed edits only; web requests never mutate NSX."""
    if not permitted(request.user):
        return HttpResponseForbidden('Administrator deletion permission is required.')
    from . import reference_cleanup as cleanup
    from .user_labels import user_label
    error = ''
    # Lock batch before item, matching confirmation, to avoid approving a queued request.
    initial = get_object_or_404(DeletionItem, pk=pk)
    with transaction.atomic():
        batch = DeletionBatch.objects.select_for_update(of=('self',)).select_related('environment', 'requested_by').get(pk=initial.batch_id)
        item = get_object_or_404(DeletionItem.objects.select_for_update(of=('self',)).select_related('finding'), pk=pk)
        item.batch = batch
        if not item.cleanup_plan:
            return HttpResponse('This item has no supported reference cleanup plan.', status=404)
        digest = cleanup.fingerprint(item)
        required_count = cleanup.requirement(item.finding) if item.finding else 2
        available = (batch.status == 'preview_ready' and item.status in ('cleanup_review', 'verified')
                     and item.verified_at and timezone.now() - item.verified_at <= timedelta(minutes=10))
        if request.method == 'POST':
            try:
                if not available: raise ValidationError('Cleanup preview expired or is no longer available. Create a new preview.')
                guard(batch, item.finding, item)
                if item.status != 'cleanup_review':
                    raise ValidationError('This plan already has its required approvals. Return to the deletion request.')
                if request.POST.get('fingerprint') != digest or request.POST.get('approve') != 'yes':
                    raise ValidationError('Review and explicitly approve the displayed rule changes.')
                if any(str(d.get('actor_id')) == str(request.user.pk) for d in item.cleanup_approvals):
                    raise ValidationError('You already approved this plan. A distinct administrator must provide the second approval when required.')
                item.cleanup_approvals.append({'actor_id': str(request.user.pk), 'actor_name': user_label(request.user),
                    'fingerprint': digest, 'at': timezone.now().isoformat()})
                item.status = 'cleanup_review'
                try:
                    cleanup.validate_approvals(item)
                    item.status = 'verified'
                    item.message = cleanup.summary(item.cleanup_plan) + '. Explicit execution confirmation is still required.'
                except ValidationError:
                    item.message = 'Reference cleanup awaits the remaining required administrator approval.'
                item.save(update_fields=['cleanup_approvals', 'status', 'message'])
                record('nsx.deletion_cleanup_approved', 'DeletionItem', item.pk, actor=request.user.pk,
                       details={'environment_id': batch.environment_id, 'batch': str(batch.pk), 'path': item.path,
                                'fingerprint': digest, 'required_approvals': required_count})
                return redirect('deletion-reference-cleanup', pk=pk)
            except ValidationError as exc:
                error = '; '.join(exc.messages)
    executing = batch.status in ('queued', 'running')
    waiting = bool(available and not batch.confirmed_at)
    active = executing or waiting
    terminal = item.status in ('deleted', 'blocked', 'failed', 'uncertain', 'already_absent')
    completed = sum(row.get('status') in ('updated', 'deleted', 'failed', 'uncertain') for row in item.cleanup_results) + int(terminal)
    phase_label = ('Waiting for deletion worker' if batch.status == 'queued' else
                   'Executing cleanup and group deletion' if batch.status == 'running' else
                   'Awaiting cleanup approvals' if waiting and item.status == 'cleanup_review' else
                   'Awaiting execution confirmation' if waiting and item.status == 'verified' else
                   'Cleanup finished — review outcomes' if terminal else 'Preview expired or closed')
    outcomes = {row['path']: row for row in item.cleanup_results}
    plan_rows = [{**change, 'outcome': outcomes.get(change['path'])} for change in item.cleanup_plan]
    template = ('inventory/reference_cleanup_progress.html' if request.method == 'GET' and request.GET.get('fragment') == '1'
                else 'inventory/reference_cleanup.html')
    response = render(request, template, {'item': item, 'batch': batch,
        'active': active, 'executing': executing, 'waiting': waiting, 'phase_label': phase_label,
        'operation_done': completed, 'operation_total': len(item.cleanup_plan) + 1,
        'approval_count': len(item.cleanup_approvals), 'plan_rows': plan_rows, 'updated_at': timezone.now(),
        'environment': batch.environment, 'fingerprint': digest, 'required_count': required_count,
        'cleanup_summary': cleanup.summary(item.cleanup_plan),
        'can_approve': available and item.status == 'cleanup_review' and not any(str(d.get('actor_id')) == str(request.user.pk) for d in item.cleanup_approvals),
        'error': error, 'expired': not available, 'configuration_json': json.dumps(item.cleanup_plan, indent=2)})
    response['Cache-Control'] = 'private, no-store'
    return response
