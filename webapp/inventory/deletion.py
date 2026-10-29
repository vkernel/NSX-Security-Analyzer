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
    from .execution_policy import executor
    actor = executor(batch) if batch.confirmed_at else batch.requested_by
    if actor: actor.refresh_from_db()
    if not permitted(actor): raise ValidationError('The responsible administrator is no longer authorized.')
    if batch.environment.manager != batch.manager: raise ValidationError('The configured NSX Manager changed. Create a new preview.')
    if not finding or finding.environment_id != batch.environment_id or finding.path != item.path or finding.revision != item.revision:
        raise ValidationError('The finding changed. Create a new preview.')
    approvals_valid(finding)


def execution_authorized(batch, item):
    """Confirmation is timely; execution has its own bounded authorization."""
    from .execution_policy import executor, can_execute
    actor = executor(batch)
    if actor: actor.refresh_from_db()
    if not can_execute(actor, item):
        raise ValidationError('The confirming administrator is no longer eligible under the execution policy or approval evidence.')
    if not batch.confirmed_at or not batch.ticket:
        raise ValidationError('Explicit confirmation and a change ticket are required.')
    if (not item.verified_at or batch.confirmed_at < item.verified_at
            or batch.confirmed_at - item.verified_at > timedelta(minutes=10)):
        raise ValidationError('Preview expired before confirmation. Create a new preview.')
    if timezone.now() - batch.confirmed_at > timedelta(hours=1):
        raise ValidationError('Execution authorization expired after one hour. Create a new preview and confirm again.')


def process_item(item, execute=False):
    batch = item.batch
    from .execution_policy import executor
    actor = executor(batch) if execute else batch.requested_by
    if not execute and (DeletionItem.objects.filter(pk=item.pk, status='cancelled').exists() or DeletionBatch.objects.filter(pk=batch.pk, status__in=['cancelling', 'cancelled']).exists()):
        return
    if execute and not DeletionItem.objects.filter(pk=item.pk, status='verified').update(status='validating'):
        return
    rechecking = not execute and item.status == 'cleanup_recheck'
    old_plan, old_before = item.cleanup_plan, item.before
    old_decisions = item.cleanup_approvals
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
                from .reference_cleanup import plan_difference
                if rechecking and (obj != old_before or plan_difference(old_plan, getattr(client, 'cleanup_plan', None))):
                    raise ValidationError('Cleanup plan or object changed. Create a new preview and obtain renewed cleanup approvals.')
                if rechecking:
                    from .reference_cleanup import validate_approvals
                    validate_approvals(item)
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
                    if rechecking:
                        item.cleanup_plan = old_plan
                        item.cleanup_approvals = old_decisions
                        item.status = 'verified'
                        item.message = 'Cleanup approved and fresh live verification passed. Confirm execution within ten minutes.'

            else:
                if obj != item.before: raise ValidationError('Object changed after preview. Create a new preview.')
                from .reference_cleanup import validate_approvals, apply_plan
                plan = getattr(client, 'cleanup_plan', None)
                if item.cleanup_plan:
                    from .reference_cleanup import plan_difference
                    differences = plan_difference(item.cleanup_plan, plan)
                    if differences:
                        item.verification['cleanup_change_diagnostics'] = {'changed_fields': differences}
                        raise ValidationError('References or rule configuration changed after preview: ' + ', '.join(differences) + '. Create a new preview.')
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
                        event(finding, 'nsx_deleted', before, 'NSX DELETE accepted and object absence verified.', actor,
                              {'deletion_batch':str(batch.pk), 'deletion_item':item.pk, 'change_ticket':batch.ticket, 'http_status':status})
                        from .related_decommission import close_related
                        close_related(finding, actor, deletion_item=item)
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
    with transaction.atomic():
        # Serialize with cancellation; stale read-only results must never revive a plan.
        locked_batch = DeletionBatch.objects.select_for_update().get(pk=batch.pk)
        locked_item = DeletionItem.objects.select_for_update().get(pk=item.pk)
        if not execute and (locked_batch.status in ('cancelling', 'cancelled') or locked_item.status == 'cancelled'):
            return
        item.save()
    record('nsx.deletion_' + item.status, 'DeletionItem', item.pk, actor=actor.pk if actor else '',
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
    rechecking = not execute and batch.items.filter(status='cleanup_recheck').exists()
    for item in batch.items.select_related('finding').order_by('pk'):
        if rechecking and item.status not in ('cleanup_recheck', 'verified'): continue
        if rechecking and item.status == 'verified' and item.cleanup_plan:
            item.status = 'cleanup_recheck'
        if DeletionBatch.objects.filter(pk=pk, status__in=['cancelling', 'cancelled']).exists(): break
        if item.status in ('cancelled', 'delegated'): continue
        item.batch = batch
        if execute and item.status != 'verified': continue
        process_item(item, execute)
    with transaction.atomic():
        locked = DeletionBatch.objects.select_for_update().get(pk=pk)
        if locked.status in ('cancelling', 'cancelled'):
            locked.status = 'cancelled'; locked.finished_at = timezone.now()
            locked.save(update_fields=['status', 'finished_at'])
            record('nsx.deletion_cancelled', 'DeletionBatch', pk, details={'environment_id': locked.environment_id})
            return
        batch.status = 'completed' if execute else 'preview_ready'
        if execute: batch.finished_at = timezone.now()
        else: batch.checked_at = timezone.now()
        batch.save()
    record('nsx.deletion_execution_finished' if execute else 'nsx.deletion_preview_finished',
           'DeletionBatch', batch.pk, actor='', details={'environment_id':batch.environment_id, 'ticket':batch.ticket,
           'message':'Processing finished; individual object outcomes determine whether deletion succeeded.'})


def interrupted(pk):
    if DeletionBatch.objects.filter(pk=pk, status='cancelling').update(status='cancelled', finished_at=timezone.now()): return
    batch = DeletionBatch.objects.filter(pk=pk, status__in=['checking','running']).first()
    if not batch: return
    batch.items.filter(status='sending').update(status='uncertain', message='Worker stopped after deletion intent was recorded. Verify NSX before retrying.')
    batch.items.filter(status__in=['pending','verified','validating','cleanup_review','cleanup_recheck']).update(status='blocked', message='Worker interrupted; create a new preview.')
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
    outcomes = {'cancelled': 'Cancelled', 'deleted': 'Deleted', 'blocked': 'Blocked', 'already_absent': 'Already absent',
                'failed': 'Failed', 'uncertain': 'Uncertain', 'verified': 'Verified',
                'pending': 'Pending', 'cleanup_review': 'Awaiting cleanup approval', 'cleanup_recheck': 'Revalidating approved cleanup', 'cleanup_rejected': 'Cleanup rejected', 'validating': 'Rechecking live evidence', 'sending': 'Deleting'}
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
            if item.status == 'verified' and not batch.confirmed_at: item.outcome_label = 'Ready to confirm'
        batch.review_label = batch.status_label
        if batch.status == 'preview_ready' and any(row.status == 'cleanup_review' for row in batch.history_items):
            batch.review_label = 'Awaiting cleanup approvals'
        elif batch.status in ('preview_queued', 'checking') and any(row.status == 'cleanup_recheck' for row in batch.history_items):
            batch.review_label = 'Revalidating approved cleanup'
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
    source = None
    remaining_ids = None
    if request.GET.get('remaining'):
        try:
            source = get_object_or_404(DeletionBatch, pk=request.GET['remaining'], environment=env)
        except (ValueError, ValidationError):
            from django.http import Http404
            raise Http404('Invalid source request')
        remaining_ids = set(source.items.exclude(status__in=['deleted', 'already_absent', 'delegated', 'cancelled'])
            .filter(finding__isnull=False).exclude(path__in=source.items.filter(status__in=['deleted', 'already_absent']).values('path')).exclude(verification__has_key='Cleanup parent item').values_list('finding_id', flat=True))
        if source.status not in ('completed', 'cancelled'):
            error = 'The original request is still active. Wait for its outcome before preparing remaining work.'
            remaining_ids = set()

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
        if remaining_ids is not None and any(not value.isdecimal() or int(value) not in remaining_ids for value in ids):
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
                    # The display deadline must also release duplicate-preview reservations.
                    # Serialize with confirmation and cleanup revalidation (batch before item).
                    paths = [row.path for row in rows]
                    candidates = DeletionBatch.objects.filter(environment=env, status='preview_ready',
                        confirmed_at__isnull=True, items__path__in=paths,
                        items__status='verified').values_list('pk', flat=True)
                    for prior in DeletionBatch.objects.select_for_update().filter(pk__in=candidates).order_by('pk'):
                        if prior.status != 'preview_ready' or prior.confirmed_at:
                            continue
                        expired_items = prior.items.filter(path__in=paths, status='verified',
                            verified_at__lte=timezone.now() - timedelta(minutes=10))
                        retired = list(expired_items.values_list('pk', flat=True))
                        if retired:
                            expired_items.update(status='blocked', message='Preview expired before confirmation. '
                                'Superseded when a new preview was requested; no execution was started.')
                            record('nsx.deletion_preview_expired', 'DeletionBatch', prior.pk, actor=request.user.pk,
                                details={'environment_id': env.pk, 'item_ids': retired, 'reason': 'new_preview_requested'})
                    existing = DeletionItem.objects.filter(batch__environment=env, path__in=[row.path for row in rows],
                        batch__status__in=['preview_queued', 'checking', 'preview_ready', 'queued', 'running'],
                        status__in=['pending', 'verified', 'cleanup_review', 'cleanup_recheck', 'validating', 'sending']).filter(Q(batch__confirmed_at__isnull=True) | Q(batch__status__in=['queued', 'running'])).order_by('-pk').first()
                    if existing:
                        from django.contrib import messages
                        messages.info(request, 'An active request already includes a selected object. Continue or cancel that request before creating another preview; no new request was created.')
                        return redirect('deletion-detail', pk=existing.batch_id)
                    batch = DeletionBatch.objects.create(environment=env, manager=env.manager, requested_by=request.user, source_request=source)
                    seen = set()
                    for row in rows:
                        if row.path in seen: continue
                        seen.add(row.path)
                        DeletionItem.objects.create(batch=batch, finding=row, revision=row.revision, path=row.path, name=row.name)
                    record('nsx.deletion_preview_requested','DeletionBatch',batch.pk, actor=request.user.pk, details={'environment_id':env.pk,'count':len(seen)})
                    return redirect('deletion-detail', pk=batch.pk)
    rows = env.findings.filter(workflow_state='ready',kind__in=KINDS)
    if remaining_ids is not None: rows = rows.filter(pk__in=remaining_ids)
    rows = list(rows.order_by('name')[:100])
    unavailable_count = len(remaining_ids - {row.pk for row in rows}) if remaining_ids is not None else 0
    return render(request,'inventory/deletion_selection.html',{'environment':env,'rows':rows,'error':error,'remaining_source':source,'unavailable_count':unavailable_count,
        })


@login_required
@require_http_methods(['GET','POST'])
def detail(request, pk):
    if not permitted(request.user): return HttpResponseForbidden('Administrator deletion permission is required.')
    error = ''
    with transaction.atomic():
        batch = get_object_or_404(DeletionBatch.objects.select_for_update(of=('self',)).select_related('environment','requested_by'), pk=pk)
        if request.method == 'POST':
            from .execution_policy import can_execute
            from .reference_cleanup import validate_approvals
            ticket = request.POST.get('ticket','').strip()
            verified = list(batch.items.filter(status='verified').select_related('finding'))
            eligible = [item for item in verified if can_execute(request.user, item)]
            requested = request.POST.getlist('execution_item')
            selected = [item for item in eligible if not requested or str(item.pk) in requested]
            if batch.status != 'preview_ready' or not batch.checked_at:
                error = 'Preview is unavailable or execution has already been queued.'
            elif not selected or (requested and set(requested) != {str(item.pk) for item in selected}):
                error = 'You are not eligible to confirm the selected objects under the current execution settings. Review the next steps below.'
            elif any(not item.verified_at or timezone.now() - item.verified_at >= timedelta(minutes=10) for item in selected):
                error = 'One or more selected object previews expired. Revalidate the plan or create a fresh preview.'
            elif not ticket or len(ticket) > 255 or request.POST.get('confirm') != 'yes':
                error = 'Provide a change ticket and explicitly confirm permanent deletion.'
            else:
                cleanup_items = [item for item in selected if item.cleanup_plan]
                try:
                    if cleanup_items and request.POST.get('confirm_cleanup') != 'yes':
                        raise ValidationError('Explicitly confirm the firewall rule changes as well as deletion.')
                    for item in cleanup_items: validate_approvals(item)
                except ValidationError as exc:
                    error = '; '.join(exc.messages)
                if not error:
                    target = batch
                    if len(selected) != len(verified) or batch.items.filter(status__in=['cleanup_review', 'cleanup_recheck']).exists():
                        target = DeletionBatch.objects.create(environment=batch.environment, manager=batch.manager,
                            requested_by=batch.requested_by, checked_at=batch.checked_at, source_request=batch)
                        for item in selected:
                            copied = DeletionItem.objects.create(batch=target, **{field: getattr(item, field) for field in
                                ('finding', 'revision', 'path', 'name', 'status', 'before', 'approved_evidence', 'approvals',
                                 'verified_at', 'verification', 'cleanup_plan', 'cleanup_approvals')})
                            item.status = 'delegated'
                            item.message = 'Included in execution request ' + str(target.pk) + '. Other objects remain pending here.'
                            item.verification = {**item.verification, 'execution_request': str(target.pk)}
                            item.save(update_fields=['status', 'message', 'verification'])
                    target.ticket, target.status, target.confirmed_at = ticket, 'queued', timezone.now()
                    target.confirmed_by = request.user
                    target.confirmed_actor_id = str(request.user.pk)
                    target.save(update_fields=['ticket','status','confirmed_at','confirmed_by','confirmed_actor_id'])
                    record('nsx.deletion_confirmed','DeletionBatch',target.pk, actor=request.user.pk,
                           details={'ticket':ticket,'environment_id':batch.environment_id,'count':len(selected),
                                    'requester_id': batch.requested_by_id, 'executor_id': request.user.pk, 'source_request': str(batch.pk)})
                    return redirect('deletion-detail',pk=target.pk)
    latest = batch.environment.snapshots.filter(testing=False, imported=False).only('pk', 'generated_at', 'summary').first()
    warnings = []
    if latest and latest.summary.get('inventory_warnings'):
        warnings = batch.environment.snapshots.filter(pk=latest.pk).values_list('report__inventory_warnings', flat=True).first() or []
    items = list(batch.items.select_related('finding'))
    from .reference_cleanup import summary as cleanup_summary
    from .execution_policy import can_execute, describe
    for item in items:
        item.execution_allowed = item.status == 'verified' and can_execute(request.user, item)
        item.execution_guidance = describe(request.user, item) if item.status == 'verified' else ''
        item.partial_cleanup = any(row.get('status') in ('updated', 'deleted') for row in item.cleanup_results) and item.status != 'deleted'
        item.cleanup_summary = cleanup_summary(item.cleanup_plan) if item.cleanup_plan else ''
        item.has_configuration = bool(item.before)
        item.has_cleanup = bool(item.cleanup_plan)
        item.cleanup_parent = item.verification.get('Cleanup parent item')
        item.related_findings = item.verification.get('Related findings', [])
        item.search_diagnostics_json = json.dumps(item.verification.get('search_diagnostics', {}), indent=2)
    counts = {status: sum(item.status == status for item in items) for status in
              ('pending', 'verified', 'validating', 'sending', 'deleted', 'blocked', 'already_absent', 'failed', 'uncertain', 'cleanup_review', 'cleanup_recheck', 'cleanup_rejected', 'cancelled')}
    active = batch.status in ('preview_queued', 'checking', 'queued', 'running', 'cancelling')
    execution = bool(batch.confirmed_at)
    done = sum(counts[status] for status in ('deleted', 'blocked', 'already_absent', 'failed', 'uncertain'))
    if not execution:
        done += counts['verified'] + counts['cleanup_review']
    for item in items:
        item.display_status = {'pending': 'Waiting for verification', 'verified': 'Ready to confirm' if not execution else 'Waiting for live checks',
            'cleanup_review': 'Awaiting cleanup approval', 'cleanup_recheck': 'Revalidating approved cleanup', 'cleanup_rejected': 'Cleanup rejected', 'validating': 'Rechecking live evidence', 'sending': 'Deleting and verifying absence',
            'cancelled': 'Cancelled', 'deleted': 'Deleted', 'blocked': 'Blocked', 'already_absent': 'Already absent',
            'failed': 'Deletion failed', 'uncertain': 'Outcome needs verification'}.get(item.status, item.status)
    for item in items:
        if (batch.status == 'preview_ready' and not execution and item.status == 'verified'
                and item.verified_at and timezone.now() - item.verified_at >= timedelta(minutes=10)):
            item.display_status = 'Preview expired'
            item.execution_allowed = False
            item.execution_guidance = ''
            item.message = 'Review window ended before execution confirmation. No changes were attempted for this object. Create a new preview.'
    ready_count = sum(item.status == 'verified' and item.display_status != 'Preview expired' for item in items)
    confirmation_items = [item for item in items if item.execution_allowed]
    stamps = [item.verified_at for item in (confirmation_items or items) if item.status == 'verified' and item.verified_at]
    deadline = (batch.confirmed_at + timedelta(hours=1) if execution else
                min(stamps) + timedelta(minutes=10) if stamps else None)
    if batch.status in ('cancelled', 'cancelling'): deadline = None
    expired = bool(deadline and timezone.now() >= deadline)
    review_expired = expired and not execution and batch.status == 'preview_ready' and not counts['cleanup_review'] and not ready_count
    if review_expired:
        for item in items:
            if item.status == 'verified':
                item.display_status = 'Preview expired'
                item.message = 'Review window ended before execution confirmation. No changes were attempted for this object. Create a new preview.'
    heading = 'Preview expired — execution never started' if review_expired else batch.status_label
    if counts['cleanup_recheck'] and not execution:
        heading = 'Revalidating approved cleanup'
    elif counts['cleanup_review'] and not execution:
        heading = 'Awaiting cleanup approvals'
    if batch.status == 'completed':
        heading = f"{counts['deleted']} deleted · {counts['blocked']} blocked"
        for key, label in [('already_absent', 'already absent'), ('failed', 'failed'), ('uncertain', 'uncertain')]:
            if counts[key]:
                heading += f" · {counts[key]} {label}"
    if any(item.partial_cleanup for item in items): heading = 'Partially completed — review remaining operations'
    elapsed = int(((batch.finished_at or (batch.checked_at if not active and not execution else None) or timezone.now()) - batch.created_at).total_seconds())
    confirmation_items = [item for item in items if item.execution_allowed]
    confirmation_expired = any(not item.verified_at or timezone.now() - item.verified_at >= timedelta(minutes=10) for item in confirmation_items)
    rule_deletes = sum(row.get('action') == 'delete_rule' for item in confirmation_items for row in item.cleanup_plan)
    context = {'batch':batch, 'environment':batch.environment, 'items':items, 'counts':counts,
        'status_label':heading, 'active':active, 'execution':execution, 'done_count':done, 'total_count':len(items),
        'review_expired': review_expired, 'poll_active': active or bool(batch.status == 'preview_ready' and ((deadline and not expired) or counts['cleanup_review'])),
        'deadline':deadline, 'expired':expired, 'updated_at':timezone.now(),
        'elapsed_label':f'{max(0, elapsed) // 60}m {max(0, elapsed) % 60}s',
        'latest_snapshot':latest, 'inventory_warnings':warnings, 'inventory_blocked':bool(latest and latest.summary.get('inventory_warnings')),
        'error':error, 'verified_count':ready_count, 'confirmation_items': confirmation_items, 'eligible_count': len(confirmation_items), 'ineligible_count': ready_count - len(confirmation_items),
        'cleanup_count':sum(len(item.cleanup_plan) for item in confirmation_items),
        'cleanup_rule_deletes':rule_deletes,
        'confirmation_object_count':len(confirmation_items) + rule_deletes,
        'cleanup_rule_updates':sum(row.get('action') != 'delete_rule' for item in confirmation_items for row in item.cleanup_plan),
        'can_cancel': batch.status in ('preview_queued', 'checking', 'preview_ready', 'queued'),
        'can_confirm':batch.status=='preview_ready' and bool(confirmation_items) and not confirmation_expired}
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
    from .execution_policy import can_execute, describe
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
        available = (batch.status == 'preview_ready' and not batch.confirmed_at and item.status == 'cleanup_review')
        if request.method == 'POST':
            try:
                if request.POST.get('action') == 'revalidate':
                    if batch.status != 'preview_ready' or batch.confirmed_at or item.status != 'verified' or not can_execute(request.user, item):
                        raise ValidationError('Only an eligible executor can revalidate this approved, unconfirmed plan.')
                    guard(batch, item.finding, item)
                    cleanup.validate_approvals(item)
                    item.status = 'cleanup_recheck'
                    item.message = 'Approved cleanup queued for fresh live verification.'
                    item.save(update_fields=['status', 'message'])
                    batch.status = 'preview_queued'; batch.save(update_fields=['status'])
                    record('nsx.deletion_cleanup_revalidation_requested', 'DeletionItem', item.pk, actor=request.user.pk,
                           details={'environment_id': batch.environment_id, 'batch': str(batch.pk), 'path': item.path})
                    return redirect('deletion-reference-cleanup', pk=pk)
                if not available: raise ValidationError('Cleanup preview expired or is no longer available. Create a new preview.')
                if request.POST.get('action') == 'reject':
                    reason = request.POST.get('reason', '').strip()[:2000]
                    if not reason: raise ValidationError('Provide a reason for rejecting the cleanup plan.')
                    item.status = 'cleanup_rejected'
                    item.message = 'Cleanup rejected: ' + reason
                    item.finished_at = timezone.now()
                    item.save(update_fields=['status', 'message', 'finished_at'])
                    record('nsx.deletion_cleanup_rejected', 'DeletionItem', item.pk, actor=request.user.pk,
                           details={'environment_id': batch.environment_id, 'batch': str(batch.pk), 'path': item.path, 'message': item.message})
                    return redirect('deletion-reference-cleanup', pk=pk)
                guard(batch, item.finding, item)
                if item.status != 'cleanup_review':
                    raise ValidationError('This plan already has its required approvals. Return to the deletion request.')
                if request.POST.get('fingerprint') != digest or request.POST.get('approve') != 'yes':
                    raise ValidationError('Review and explicitly approve the displayed rule changes.')
                if any(str(d.get('actor_id')) == str(request.user.pk) for d in item.cleanup_approvals):
                    raise ValidationError('You already approved this plan. A distinct administrator must provide the second approval when required.')
                # Validate existing decisions before accepting another one. Invalid evidence
                # or revoked permissions must not masquerade as a missing reviewer.
                cleanup.validate_approvals(item, require_complete=False)
                decisions = list(item.cleanup_approvals)
                item.cleanup_approvals = decisions + [{'actor_id': str(request.user.pk), 'actor_name': user_label(request.user),
                    'fingerprint': digest, 'at': timezone.now().isoformat()}]
                item.status = 'cleanup_review'
                if len({str(d.get('actor_id')) for d in item.cleanup_approvals}) >= required_count:
                    try:
                        cleanup.validate_approvals(item)
                    except ValidationError:
                        item.cleanup_approvals = decisions
                        raise
                    item.status = 'cleanup_recheck'
                    item.message = 'Required cleanup approvals recorded. Fresh live verification is queued; no changes have been authorized.'
                    batch.status = 'preview_queued'
                    batch.save(update_fields=['status'])
                else:
                    item.message = 'Your cleanup approval is recorded. A different administrator must provide the remaining approval.'
                item.save(update_fields=['cleanup_approvals', 'status', 'message'])
                record('nsx.deletion_cleanup_approved', 'DeletionItem', item.pk, actor=request.user.pk,
                       details={'environment_id': batch.environment_id, 'batch': str(batch.pk), 'path': item.path,
                                'fingerprint': digest, 'required_approvals': required_count})
                return redirect('deletion-reference-cleanup', pk=pk)
            except ValidationError as exc:
                error = '; '.join(exc.messages)
    revalidating = item.status == 'cleanup_recheck' and batch.status in ('preview_queued', 'checking')
    executing = batch.status in ('queued', 'running') and item.status not in ('cancelled', 'cleanup_rejected')
    confirmation_ready = (batch.status == 'preview_ready' and item.status == 'verified' and item.verified_at
                          and timezone.now() - item.verified_at < timedelta(minutes=10) and not batch.confirmed_at)
    waiting = bool(available or confirmation_ready)
    active = executing or waiting or revalidating
    terminal = item.status in ('deleted', 'blocked', 'failed', 'uncertain', 'already_absent', 'cleanup_rejected', 'cancelled')
    completed = sum(row.get('status') in ('updated', 'deleted', 'failed', 'uncertain') for row in item.cleanup_results) + int(terminal)
    phase_label = ('Waiting for deletion worker' if batch.status == 'queued' else
                   'Executing cleanup and group deletion' if batch.status == 'running' else
                   'Awaiting cleanup approvals' if waiting and item.status == 'cleanup_review' else
                   'Awaiting execution confirmation' if waiting and item.status == 'verified' else
                   'Cleanup finished — review outcomes' if terminal else 'Preview expired or closed')
    deadline = item.verified_at + timedelta(minutes=10) if item.verified_at and item.status == 'verified' else None
    review_expired = bool(not batch.confirmed_at and not terminal and deadline and timezone.now() >= deadline)
    closed = not executing and not waiting and not terminal and not revalidating
    phase_message = item.message
    if review_expired:
        phase_label = 'Preview expired — execution never started'
        phase_message = ('The ten-minute review window ended before all approvals and final confirmation were completed. '
                         'No firewall changes or group deletion were attempted. Revalidate the approved plan or create a new preview.')
    elif revalidating:
        phase_label = 'Revalidating approved cleanup'
        phase_message = 'Fresh live checks are queued or running. No NSX changes are made during verification.'
    elif item.status in ('cleanup_rejected', 'cancelled'):
        phase_label = 'Cleanup cancelled' if item.status == 'cancelled' else 'Cleanup rejected'
    elif closed:
        phase_label = 'Cleanup not scheduled'
        phase_message = 'This plan is no longer available for execution. Create a new preview to continue.'
    elif waiting and item.status == 'cleanup_review':
        phase_message = f'{max(0, required_count - len(item.cleanup_approvals))} more distinct administrator approval(s) required. Execution has not started.'
    elif waiting:
        phase_message = describe(request.user, item) + ' Final confirmation and a change ticket are still required. Execution has not started.'
    approved_by_me = any(str(d.get('actor_id')) == str(request.user.pk) for d in item.cleanup_approvals)
    if waiting and item.status == 'cleanup_review' and approved_by_me:
        phase_message = 'Your approval is recorded. Waiting for a different eligible administrator; you cannot provide the second approval.'
    if error:
        active = False
        phase_label = 'Approval action could not be completed'
        phase_message = error
    outcomes = {row['path']: row for row in item.cleanup_results}
    plan_rows = [{**change, 'readable_changes': cleanup.readable_changes(change), 'outcome': outcomes.get(change['path'])} for change in item.cleanup_plan]
    template = ('inventory/reference_cleanup_progress.html' if request.method == 'GET' and request.GET.get('fragment') == '1'
                else 'inventory/reference_cleanup.html')
    response = render(request, template, {'item': item, 'batch': batch,
        'active': active, 'executing': executing, 'waiting': waiting, 'phase_label': phase_label,
        'approved_by_me': approved_by_me, 'is_requester': batch.requested_by_id == request.user.pk, 'eligible_executor': can_execute(request.user, item),
        'phase_message': phase_message, 'review_expired': review_expired, 'closed': closed or item.status in ('cleanup_rejected', 'cancelled'), 'deadline': deadline,
        'can_revalidate': item.status == 'verified' and batch.status == 'preview_ready' and not batch.confirmed_at and can_execute(request.user, item),
        'partial_cleanup': item.status != 'deleted' and any(row.get('status') in ('updated', 'deleted') for row in item.cleanup_results),
        'show_operations': bool(batch.confirmed_at or item.cleanup_results),
        'operation_done': completed, 'operation_total': len(item.cleanup_plan) + 1,
        'approval_count': len(item.cleanup_approvals), 'plan_rows': plan_rows, 'updated_at': timezone.now(),
        'environment': batch.environment, 'fingerprint': digest, 'required_count': required_count,
        'cleanup_summary': cleanup.summary(item.cleanup_plan),
        'can_cancel': batch.status in ('preview_queued', 'checking', 'preview_ready', 'queued') and item.status in ('cleanup_review', 'cleanup_recheck', 'verified') and not item.cleanup_results,
        'can_approve': available and item.status == 'cleanup_review' and not any(str(d.get('actor_id')) == str(request.user.pk) for d in item.cleanup_approvals),
        'error': error, 'expired': not available, 'configuration_json': json.dumps(item.cleanup_plan, indent=2)})
    response['Cache-Control'] = 'private, no-store'
    return response
