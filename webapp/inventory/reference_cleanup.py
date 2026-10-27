"""Explicitly approved, revision-checked DFW reference cleanup. No mutation retries."""
from copy import deepcopy
import hashlib
import json
from urllib.parse import quote
import urllib3
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from .approval_modes import required
from .usability import policy
from .audit_events import record


def requirement(finding):
    settings = policy()
    return max(finding.required_approvals, required('empty_group', settings), required('disabled', settings))


def fingerprint(item):
    return hashlib.sha256(json.dumps({'plan': item.cleanup_plan, 'group': item.before,
        'finding': item.finding_id, 'revision': item.revision}, sort_keys=True).encode()).hexdigest()


def protected(obj):
    return (obj.get('_system_owned') or obj.get('is_default') or obj.get('marked_for_delete')
            or obj.get('_protection') not in (None, 'NOT_PROTECTED') or obj.get('remote_path'))


def build_plan(client, group, refs, resources=None):
    from .deletion import target_type
    from . import collector
    if len(refs) > 100:
        raise ValidationError('More than 100 dependent objects. Resolve references separately.')
    if protected(group):
        raise ValidationError('Only unprotected local groups support reference cleanup.')
    changes = []
    for path in sorted(refs):
        try:
            supported = target_type(path) == 'Rule'
        except ValidationError:
            supported = False
        if not supported:
            raise ValidationError(f'Reference cleanup requires manual resolution: {path} is not a supported local DFW rule.')
        rule = client.get(path)
        parent_path = path.rsplit('/rules/', 1)[0]
        parent = client.get(parent_path)
        if (rule.get('path') != path or rule.get('resource_type') != 'Rule'
                or parent.get('path') != parent_path or parent.get('resource_type') != 'SecurityPolicy'
                or protected(rule) or protected(parent) or rule.get('children')):
            raise ValidationError(f'Reference cleanup is unavailable for protected or unsupported rule/policy: {path}.')
        if (type(rule.get('_revision')) is not int or not rule.get('unique_id')
                or rule.get('_create_time') is None or type(parent.get('_revision')) is not int):
            raise ValidationError(f'Rule or policy identity/revision is incomplete: {path}.')
        if rule.get('sources_excluded') not in (None, False) or rule.get('destinations_excluded') not in (None, False):
            raise ValidationError(f'Negated groups require a separate firewall review: {path}.')
        proposed = deepcopy(rule)
        fields = []
        delete_rule = False
        for key, label in [('source_groups', 'Source'), ('destination_groups', 'Destination')]:
            values = rule.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValidationError(f'Unsupported {label.lower()} definition: {path}.')
            if group['path'] not in values:
                continue
            remaining = [v for v in values if v != group['path']]
            if not remaining:
                delete_rule = True
            # Initial scope deliberately limited to ordinary local Group paths.
            try:
                ordinary = all(target_type(v) == 'Group' for v in remaining)
            except ValidationError:
                ordinary = False
            if not ordinary:
                raise ValidationError(f'{label} of {path} includes ANY, IP literals or unsupported group paths. Resolve separately.')
            proposed[key] = remaining
            fields.append({'field': key, 'label': label, 'before': values, 'after': remaining})
        if not fields or collector.collect_references([proposed], [group])[group['path']]:
            raise ValidationError(f'Applied To or another unsupported reference remains in {path}. Resolve it separately.')
        if delete_rule:
            rule_dependencies(rule, resources)
        change = {'path': path, 'name': rule.get('display_name') or rule.get('id') or path,
            'policy_name': parent.get('display_name') or parent.get('id') or parent_path,
            'enabled': not rule.get('disabled', False), 'fields': fields, 'before': rule,
            'after': proposed, 'policy': {k: v for k, v in parent.items() if k not in ('rules', 'children')}}
        if delete_rule:
            change.update(action='delete_rule', after=None,
                reason='The verified empty group is the only group in ' + ', '.join(f['label'].lower() for f in fields if not f['after']) + '.')
        changes.append(change)
    return changes



def counts(plan):
    deleted = sum(change.get('action') == 'delete_rule' for change in plan)
    return {'updates': len(plan) - deleted, 'deletes': deleted}


def summary(plan):
    totals = counts(plan)
    parts = []
    if totals['updates']:
        parts.append(f"Modify {totals['updates']} firewall rule(s)")
    if totals['deletes']:
        parts.append(f"Delete {totals['deletes']} firewall rule(s)")
    return ', '.join(parts) + ', then delete 1 group'


def rule_dependencies(rule, resources):
    from . import collector
    if resources is None or not any(r.get('path') == rule['path'] for r in resources):
        raise ValidationError('The live search index does not contain the dependent rule; deletion is blocked.')
    refs = collector.collect_references(resources, [rule])[rule['path']]
    if refs:
        raise ValidationError('Dependent rule is itself referenced by: ' + ', '.join(sorted(refs)[:10]))
    if any(r.get('path', '').startswith(rule['path'] + '/') for r in resources):
        raise ValidationError('Dependent rule has child configuration; cascading deletion is blocked.')


def verify_rule_dependencies(client, rule):
    from . import collector
    domains = collector.objects(client, '/infra/domains')
    groups = [g for domain in domains for g in collector.objects(client, domain['path'] + '/groups')]
    services = collector.objects(client, '/infra/services')
    resources, coverage = collector.verified_configuration_search(client, groups, services)
    client.search_diagnostics = coverage
    if coverage.get('mode') != 'all_types' or coverage.get('errors') or coverage.get('failed') or getattr(client, 'inventory_warnings', []):
        raise ValidationError('Live dependency coverage for the rule is incomplete; rule deletion is blocked.')
    rule_dependencies(rule, resources + groups + services)


def rule_intent(item, change):
    from .models import DeletionItem
    # A separate row supplies durable deletion identity and object-level history.
    if DeletionItem.objects.filter(batch=item.batch, path=change['path']).exists():
        raise ValidationError('This rule already has a deletion item in the batch. Create a separate preview: ' + change['path'])
    return DeletionItem.objects.create(batch=item.batch, path=change['path'], name=change['name'], revision=0,
        status='sending', before=change['before'],
        approved_evidence={'unique_id': change['before']['unique_id'], 'created_at': change['before']['_create_time'],
                           'configuration': {'rule': change['before'], 'policy': change['policy']}},
        approvals={'reference_cleanup': item.cleanup_approvals},
        verification={'Cleanup parent item': item.pk, 'Cleanup parent group': item.path},
        request_sent_at=timezone.now(), message='Rule deletion authorized by the approved group reference-cleanup plan.')


def finish_rule(child, item, outcome):
    from django.db import transaction
    from .models import Environment, Finding
    from .finding_workflow import event
    with transaction.atomic():
        Environment.objects.select_for_update().get(pk=item.batch.environment_id)
        child.status = outcome['status']
        child.http_status = outcome.get('http_status')
        child.finished_at = timezone.now()
        child.message = ('Deleted from NSX through group reference cleanup; absence verified.' if child.status == 'deleted'
                         else outcome.get('message') or 'Rule deletion ' + child.status + '; verify NSX before another attempt. No automatic retry.')
        if child.status == 'deleted':
            rows = Finding.objects.select_for_update().filter(environment_id=item.batch.environment_id,
                path=child.path, evidence__unique_id=child.approved_evidence['unique_id'],
                evidence__created_at=child.approved_evidence['created_at']).order_by('pk')
            for finding in rows:
                if child.finding_id is None:
                    child.finding = finding
                    child.revision = finding.revision
                if finding.workflow_state == 'decommissioned':
                    continue
                before = finding.workflow_state
                finding.workflow_state = 'decommissioned'
                finding.present = False
                finding.change_ticket = item.batch.ticket
                finding.revision += 1
                finding.save(update_fields=['workflow_state', 'present', 'change_ticket', 'revision'])
                event(finding, 'nsx_deleted', before, 'Rule deleted through an explicitly approved group cleanup plan; absence verified.',
                      item.batch.requested_by, {'deletion_item': child.pk, 'deletion_batch': str(item.batch_id),
                      'primary_finding': item.finding_id, 'cleanup_parent_item': item.pk, 'change_ticket': item.batch.ticket})
        child.save()
        record('nsx.deletion_' + child.status, 'DeletionItem', child.pk, actor=item.batch.requested_by_id,
               details={'environment_id': item.batch.environment_id, 'batch': str(item.batch_id),
                        'name': child.name, 'path': child.path, 'ticket': item.batch.ticket,
                        'http_status': child.http_status, 'cleanup_parent_item': item.pk, 'message': child.message})


def validate_approvals(item):
    from .deletion import permitted
    required_count = requirement(item.finding)
    digest = fingerprint(item)
    decisions = item.cleanup_approvals
    actors = set()
    for decision in decisions:
        if decision.get('fingerprint') != digest:
            raise ValidationError('Reference cleanup plan changed; new approval is required.')
        actor = get_user_model().objects.filter(pk=decision.get('actor_id')).first()
        if not permitted(actor):
            raise ValidationError('A reference cleanup reviewer is no longer authorized.')
        actors.add(str(actor.pk))
    if required_count not in (1, 2) or len(actors) < required_count:
        raise ValidationError(f'Reference cleanup needs {required_count} distinct administrator approval(s) for the exact rule changes.')


def apply_plan(client, item, authorize):
    """Caller freshly checked all references/membership. Persist every mutation intent."""
    from .deletion import current_object, clean, delete_once, execution_authorized
    if item.cleanup_results:
        raise ValidationError('Reference cleanup was already attempted. Review recorded outcomes; create a fresh preview.')
    for change in item.cleanup_plan:
        removing = change.get('action') == 'delete_rule'
        if removing:
            verify_rule_dependencies(client, change['before'])
        authorize()
        current = current_object(client, change['path'])
        parent = client.get(change['path'].rsplit('/rules/', 1)[0])
        # Updating a sibling can increment policy revision; compare policy configuration without embedded rules.
        policy_config = lambda p: {k: v for k, v in clean(p).items() if k not in ('rules', 'children')}
        if current != change['before'] or policy_config(parent) != policy_config(change['policy']) or protected(parent):
            raise ValidationError('Rule or parent policy changed after approval: ' + change['path'])
        execution_authorized(item.batch, item)
        validate_approvals(item)
        child = rule_intent(item, change) if removing else None
        outcome = {'path': change['path'], 'action': 'delete_rule' if removing else 'update_rule',
                   'status': 'sending', 'started_at': timezone.now().isoformat()}
        if child:
            outcome['deletion_item'] = child.pk
        item.cleanup_results.append(outcome)
        item.save(update_fields=['cleanup_results'])
        record('nsx.deletion_cleanup_sending', 'DeletionItem', item.pk, actor=item.batch.requested_by_id,
               details={'environment_id': item.batch.environment_id, 'batch': str(item.batch_id), 'path': change['path'], 'ticket': item.batch.ticket})
        try:
            if removing:
                status = delete_once(client, change['path'])
                outcome['http_status'] = status
                if status not in (200, 202, 204):
                    outcome['status'] = 'failed' if status in (400, 401, 403, 405, 409, 412, 429) else 'uncertain'
                    from .collector import access_message
                    raise ValidationError((access_message('DELETE', change['path'], status) if status in (401, 403) else f'Rule DELETE returned HTTP {status}: {change["path"]}.') + ' No retry performed.')
                if current_object(client, change['path']) is not None:
                    outcome['status'] = 'uncertain'
                    raise ValidationError('Rule DELETE accepted, but absence is not verified: ' + change['path'])
                outcome['status'] = 'deleted'
            else:
                # Preserve all configuration fields and send the exact approved revision. No PATCH/force.
                payload = {k: v for k, v in change['after'].items() if not k.startswith('_') or k == '_revision'}
                response = client.http.request('PUT', client.base_url + quote(change['path'], safe='/'),
                    body=json.dumps(payload).encode(), headers={**client.headers, 'Content-Type': 'application/json'},
                    retries=False, redirect=False, timeout=urllib3.Timeout(connect=client.timeout, read=client.timeout),
                    pool_timeout=client.timeout)
                from .collector import record_access_failure, access_message
                record_access_failure(client, 'PUT', change['path'], response.status)
                outcome['http_status'] = response.status
                if response.status != 200:
                    outcome['status'] = 'failed' if response.status in (400, 401, 403, 405, 409, 412, 429) else 'uncertain'
                    raise ValidationError((access_message('PUT', change['path'], response.status) if response.status in (401, 403) else f'Rule update returned HTTP {response.status}: {change["path"]}.') + ' No retry performed.')
                result = current_object(client, change['path'])
                if result is None or clean(result) != clean(change['after']) or result.get('_create_time') != change['before'].get('_create_time'):
                    outcome['status'] = 'uncertain'
                    raise ValidationError('Rule update could not be verified: ' + change['path'])
                outcome['status'] = 'updated'
        except Exception as exc:
            from .deletion import describe_error
            outcome['message'] = describe_error(exc)
            if outcome['status'] == 'sending': outcome['status'] = 'uncertain'
            raise
        finally:
            outcome['finished_at'] = timezone.now().isoformat()
            if child:
                finish_rule(child, item, outcome)
            item.save(update_fields=['cleanup_results'])
            record('nsx.deletion_cleanup_' + outcome['status'], 'DeletionItem', item.pk,
                   actor=item.batch.requested_by_id, details={'environment_id': item.batch.environment_id, 'batch': str(item.batch_id),
                   'path': change['path'], 'ticket': item.batch.ticket, 'http_status': outcome.get('http_status')})
