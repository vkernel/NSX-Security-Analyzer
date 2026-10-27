"""Bounded, explicit bulk operations. Never sends NSX mutation requests."""
import json
import logging
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import transaction, DatabaseError, connection
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Finding, Environment, DeletionItem, DeletionBatch, AuditJob, SnapshotRecord, Snapshot
from .forms import BulkAssignmentForm
from .audit_events import record

ACTIONS = {
    'findings': {'assign_me': 'Assign to me', 'reassign': 'Reassign owner', 'release': 'Return to unassigned', 'export': 'Export selected evidence', 'preview': 'Preview deletion from NSX'},
    'cleanup': {'cancel': 'Cancel selected cleanup plans', 'export': 'Export selected plans'},
    'history': {'export': 'Export selected outcomes', 'configurations': 'Download saved configurations'},
    'collections': {'cancel': 'Cancel queued collections', 'export': 'Export selected diagnostics'},
    'inventory': {'export': 'Export selected objects', 'compare': 'Compare selected details', 'reviews': 'View related finding reviews'},
}


def destination(request):
    # Only application-relative destinations, never an external redirect.
    value = request.POST.get('return_to', '/')
    return value if value.startswith('/') and not value.startswith('//') and '\\' not in value and '\r' not in value and '\n' not in value else '/'


def download(kind, action, data, actor):
    record('bulk.exported', details={'kind': kind, 'action': action, 'count': len(data)}, actor=actor.pk)
    response = HttpResponse(json.dumps(data, indent=2, default=str), content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="nsx-{kind}-{action}.json"'
    response['Cache-Control'] = 'private, no-store'
    return response


def description(kind, obj):
    if kind == 'findings': return obj.name, obj.environment.name, obj.path
    if kind == 'cleanup': return obj.name, obj.batch.environment.name, obj.path
    if kind == 'history': return str(obj.pk), obj.environment.name, obj.created_at
    if kind == 'collections': return str(obj.pk), obj.environment.name, obj.get_status_display()
    return obj.name, obj.snapshot.environment.name, obj.compact.get('path', '')


def load(kind, ids, request):
    if kind == 'findings': return Finding.objects.select_related('environment', 'owner').filter(pk__in=ids)
    if kind == 'cleanup': return DeletionItem.objects.select_related('batch__environment').filter(pk__in=ids)
    if kind == 'history': return DeletionBatch.objects.select_related('environment').filter(pk__in=ids)
    if kind == 'collections': return AuditJob.objects.select_related('environment').defer('config', 'environment__password_ciphertext').filter(pk__in=ids)
    return SnapshotRecord.objects.select_related('snapshot__environment').filter(snapshot_id=request.POST.get('snapshot'), ordinal__in=ids, view='inventory')


@login_required
@require_POST
def bulk(request, kind):
    action = request.POST.get('action', '')
    if kind not in ACTIONS or action not in ACTIONS[kind]: return HttpResponseBadRequest('Unsupported bulk action.')
    if not request.user.is_active or (kind != 'inventory' and not request.user.is_staff):
        return HttpResponseForbidden('Operator access required.')
    if kind in ('cleanup', 'history') or (kind == 'findings' and action == 'preview'):
        from .deletion import permitted
        if not permitted(request.user): return HttpResponseForbidden('Administrator access required.')
    raw = list(dict.fromkeys(request.POST.getlist('selected')))
    if not 1 <= len(raw) <= 100 or any(len(value) > 100 for value in raw): return HttpResponseBadRequest('Select 1–100 items on the current page.')
    try:
        ids = [value.split(':')[0] for value in raw]
        objects = list(load(kind, ids, request))
    except (ValueError, ValidationError): return HttpResponseBadRequest('Invalid selection. Reload the page.')
    by_id = {str(obj.ordinal if kind == 'inventory' else obj.pk): obj for obj in objects}
    rows = []
    for value in raw:
        obj = by_id.get(value.split(':')[0])
        name, environment, detail = description(kind, obj) if obj else ('Unavailable item', '', value)
        rows.append({'value': value, 'object': obj, 'name': name, 'environment': environment, 'detail': detail})
    back = destination(request)
    form = BulkAssignmentForm(request.POST if request.POST.get('confirm') else None, initial={'owner': request.user.pk})
    context = {'kind': kind, 'action': action, 'title': ACTIONS[kind][action], 'rows': rows, 'back': back, 'assignment_form': form,
               'snapshot_id': request.POST.get('snapshot', ''), 'reason': request.POST.get('reason', ''),
               'needs_reason': action in ('release', 'reassign', 'cancel'), 'needs_owner': action == 'reassign'}
    if kind == 'inventory' and action in ('compare', 'reviews'):
        for row in rows:
            obj = row['object']
            if obj:
                row['configuration'] = json.dumps(obj.data, indent=2, default=str)
                row['reviews'] = Finding.objects.filter(environment_id=obj.snapshot.environment_id, path=obj.compact.get('path', '')).order_by('kind')
        context['comparison'] = [{'label': key.replace('_', ' ').capitalize(), 'values': [json.dumps(row['object'].compact.get(key, ''), ensure_ascii=False) if row['object'] else 'Unavailable' for row in rows]} for key in ('name', 'kind', 'path', 'usage', 'membership', 'references')]
        context['read_only'] = True
        return render(request, 'inventory/bulk_review.html', context)
    if action in ('export', 'configurations'):
        data = []
        for row in rows:
            obj = row['object']
            entry = {'name': row['name'], 'environment': row['environment'], 'selection': row['value']}
            if not obj: entry['error'] = 'No longer available'
            elif kind == 'findings':
                entry.update(path=obj.path, kind=obj.kind, workflow_state=obj.workflow_state, evidence=obj.evidence, approvals=obj.approvals, revision=obj.revision)
            elif kind == 'cleanup':
                entry.update(path=obj.path, status=obj.status, plan=obj.cleanup_plan, approvals=obj.cleanup_approvals, outcomes=obj.cleanup_results)
            elif kind == 'history':
                entry.update(ticket=obj.ticket, status=obj.status, objects=[])
                for item in obj.items.order_by('pk'):
                    saved = {'name': item.name, 'path': item.path, 'status': item.status, 'message': item.message, 'finished_at': item.finished_at, 'cleanup_outcomes': item.cleanup_results}
                    if action == 'configurations': saved.update(configuration=item.before, cleanup_plan=item.cleanup_plan, configuration_available=bool(item.before))
                    entry['objects'].append(saved)
            elif kind == 'collections':
                entry.update(status=obj.status, diagnostics=obj.diagnostics)
                summary = Snapshot.objects.filter(job_id=obj.pk).values('report__performance', 'report__dfw__collection_diagnostics').first()
                entry['request_statistics'] = summary
            else: entry.update(snapshot=str(obj.snapshot_id), collected_at=obj.snapshot.generated_at, data=obj.data)
            data.append(entry)
        return download(kind, action, data, request.user)
    if action == 'preview':
        available = [row['object'] for row in rows if row['object']]
        if len(available) != len(raw) or len({obj.environment_id for obj in available}) != 1:
            context['error'] = 'Select available findings from one environment for deletion preview.'
            context['cannot_execute'] = True
        else:
            # The existing read-only preview endpoint rechecks status, permissions and revisions.
            from .deletion import selection
            return selection(request, available[0].environment_id)
    if request.POST.get('confirm') == 'yes' and not context.get('cannot_execute'):
        reason = request.POST.get('reason', '').strip()
        if context['needs_reason'] and not 1 <= len(reason) <= 2000:
            context['error'] = 'Provide a reason of 1–2000 characters.'
        elif action == 'reassign' and not form.is_valid():
            context['error'] = 'Choose an active operator or administrator.'
        else:
            results = []
            for row in rows:
                obj = row['object']
                try:
                    if not obj: raise ValidationError('Item is no longer available.')
                    with transaction.atomic():
                        if kind == 'findings':
                            from .finding_workflow import decide
                            Environment.objects.select_for_update().get(pk=obj.environment_id)
                            current = Finding.objects.select_for_update().get(pk=obj.pk)
                            try: revision = int(row['value'].split(':')[1])
                            except (IndexError, ValueError): raise ValidationError('Missing revision; reload the list.')
                            if current.revision != revision: raise ValidationError('Changed since selection; reload before retrying.')
                            if action == 'assign_me':
                                if current.owner_id: raise ValidationError('Already assigned; existing owner retained.')
                                decide(current, request.user, 'assign', '', owner=request.user)
                            elif action == 'release': decide(current, request.user, 'unassign', reason)
                            elif action == 'reassign':
                                if not current.owner_id: raise ValidationError('No longer assigned; use Assign owner.')
                                if current.owner_id != request.user.pk and not request.user.is_superuser: raise ValidationError('Only the owner or an administrator can bulk reassign this finding.')
                                decide(current, request.user, 'assign', reason, owner=form.cleaned_data['owner'])
                        elif kind == 'cleanup':
                            from .task_cancellation import DELETION_OPEN, ITEM_OPEN
                            batch = DeletionBatch.objects.select_for_update().get(pk=obj.batch_id)
                            current = DeletionItem.objects.select_for_update().get(pk=obj.pk)
                            if not current.cleanup_plan or batch.status not in DELETION_OPEN or current.status not in ITEM_OPEN or current.cleanup_results:
                                raise ValidationError('Plan finished or execution started; cancellation is unavailable.')
                            current.status = 'cancelled'; current.message = 'Cleanup plan cancelled. ' + reason; current.finished_at = timezone.now()
                            current.save(update_fields=['status', 'message', 'finished_at'])
                            record('nsx.deletion_cleanup_cancelled', 'DeletionItem', current.pk, actor=request.user.pk,
                                   details={'environment_id': batch.environment_id, 'batch': str(batch.pk), 'reason': reason, 'notify_reviewers': bool(current.cleanup_approvals), 'message': current.message, 'status': current.status})
                        elif kind == 'collections':
                            queued = AuditJob.objects.filter(pk=obj.pk, status='queued')
                            queued = queued.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else queued.select_for_update()
                            current = queued.first()
                            if current is None: raise ValidationError('Not queued or currently being claimed. Running collections must be stopped individually.')
                            from .services import request_collection_stop
                            request_collection_stop(current.pk, request.user)
                            record('collection.bulk_cancelled', 'AuditJob', current.pk, actor=request.user.pk, details={'reason': reason})
                    results.append({**row, 'result': 'Succeeded', 'message': 'Assignment updated.' if kind == 'findings' else 'Cancelled. History retained.'})
                except ObjectDoesNotExist:
                    results.append({**row, 'result': 'Skipped', 'message': 'Item was removed while processing the selection.'})
                except DatabaseError:
                    logging.getLogger(__name__).exception('Bulk %s %s failed for selected item %s', kind, action, row['value'])
                    results.append({**row, 'result': 'Failed', 'message': 'Database operation failed. Refresh the item and review its history before retrying.'})
                except ValidationError as exc:
                    results.append({**row, 'result': 'Skipped', 'message': '; '.join(exc.messages)})
            context.update(results=results, succeeded=sum(r['result']=='Succeeded' for r in results), skipped=sum(r['result']=='Skipped' for r in results), failed=sum(r['result']=='Failed' for r in results))
    response = render(request, 'inventory/bulk_review.html', context)
    response['Cache-Control'] = 'private, no-store'
    return response
