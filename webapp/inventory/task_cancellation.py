"""Explicit cancellation; locks match worker claim/publication boundaries."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import DeletionBatch, DeletionItem, SnapshotComparison, FindingRecalculation
from .audit_events import record

DELETION_OPEN = ('preview_queued', 'checking', 'preview_ready', 'queued')
ITEM_OPEN = ('pending', 'verified', 'cleanup_review', 'cleanup_recheck')


@login_required
@require_http_methods(['GET', 'POST'])
def cancel(request, kind, pk):
    from .deletion import permitted
    if kind in ('deletion', 'cleanup'):
        allowed = permitted(request.user)
    else:
        allowed = request.user.is_active and request.user.is_superuser
    if not allowed:
        return HttpResponseForbidden('Administrator permission is required to cancel this task.')
    if kind not in ('cleanup', 'deletion', 'comparison', 'recalculation'):
        return HttpResponseForbidden('Unsupported task type.')
    with transaction.atomic():
        if kind == 'cleanup':
            initial = get_object_or_404(DeletionItem, pk=pk)
            batch = DeletionBatch.objects.select_for_update().get(pk=initial.batch_id)
            obj = DeletionItem.objects.select_for_update().get(pk=pk)
            available = bool(obj.cleanup_plan and batch.status in DELETION_OPEN and obj.status in ITEM_OPEN and not obj.cleanup_results)
            title = 'Cancel cleanup plan'
            description = 'Only this object’s cleanup plan is withdrawn. No firewall changes or group deletion will be performed through this plan. Other objects in the request are unaffected. Original finding approvals are retained.'
            back = reverse('deletion-reference-cleanup', args=[pk])
            environment_id = batch.environment_id
            target_label, target_path = obj.name, obj.path
        elif kind == 'deletion':
            obj = get_object_or_404(DeletionBatch.objects.select_for_update(), pk=pk)
            available = obj.status in DELETION_OPEN and not obj.items.exclude(cleanup_results=[]).exists()
            title = 'Cancel deletion request'
            description = 'Cancel all objects in this request. No NSX changes will be performed through this request. Any read-only check in progress will stop after its current check; late results cannot authorize deletion.'
            back = reverse('deletion-detail', args=[pk])
            environment_id = obj.environment_id
            target_label = ', '.join(obj.items.values_list('name', flat=True))
            target_path = str(obj.pk)
        elif kind == 'comparison':
            obj = get_object_or_404(SnapshotComparison.objects.select_for_update(), pk=pk)
            available = obj.status in ('queued', 'running') and not obj.ready
            title = 'Cancel comparison'
            description = 'Stop preparing this comparison. Saved snapshots and other completed comparisons remain available.'
            target_label = 'Snapshot comparison'
            target_path = str(obj.before_id) + ' → ' + str(obj.after_id)
            environment_id = obj.after.environment_id
            back = reverse('snapshot-comparison', args=[environment_id]) + f'?before={obj.before_id}&after={obj.after_id}'
        elif kind == 'recalculation':
            obj = get_object_or_404(FindingRecalculation.objects.select_for_update(), pk=pk)
            available = obj.status in ('queued', 'running')
            title = 'Cancel review recalculation'
            description = 'Stop recalculation at a safe checkpoint. Previously published finding results and review decisions remain available. Save criteria again to request a new recalculation.'
            environment_id = obj.environment_id
            target_label, target_path = 'Finding review recalculation', ', '.join(obj.kinds)
            back = reverse('findings', args=[environment_id])
        else:
            return HttpResponseForbidden('Unsupported task type.')
        if request.method == 'POST':
            if not available:
                messages.error(request, 'This task can no longer be cancelled. It has finished or execution/publication has started. Review its current outcomes.')
            elif request.POST.get('confirm') != 'yes':
                messages.error(request, 'Explicitly confirm cancellation.')
            else:
                reason = request.POST.get('reason', '').strip()[:2000]
                notify_reviewers = kind == 'cleanup' and obj.status == 'cleanup_review' and bool(obj.cleanup_approvals)
                waiting = (kind == 'deletion' and obj.status == 'checking') or (kind in ('comparison', 'recalculation') and obj.status == 'running')
                obj.status = 'cancelling' if waiting else 'cancelled'
                fields = ['status']
                if kind == 'cleanup':
                    obj.message = 'Cleanup plan cancelled. ' + reason
                    obj.finished_at = timezone.now()
                    fields += ['message', 'finished_at']
                elif kind == 'deletion':
                    obj.items.filter(status__in=ITEM_OPEN).update(status='cancelled', message='Deletion request cancelled. ' + reason, finished_at=timezone.now())
                    if not waiting:
                        obj.finished_at = timezone.now(); fields.append('finished_at')
                elif kind == 'recalculation' and not waiting:
                    obj.finished_at = timezone.now(); fields.append('finished_at')
                obj.save(update_fields=fields)
                record('nsx.deletion_cleanup_cancelled' if kind == 'cleanup' else 'nsx.deletion_cancel_requested' if kind == 'deletion' else kind + '.cancel_requested',
                       type(obj).__name__, obj.pk, actor=request.user.pk,
                       details={'environment_id': environment_id, 'reason': reason, 'notify_reviewers': notify_reviewers, 'message': title + (': ' + reason if reason else ''), 'status': obj.status, 'batch': str(batch.pk) if kind == 'cleanup' else str(obj.pk) if kind == 'deletion' else ''})
                messages.success(request, 'Cancellation requested. Waiting for the worker to stop.' if waiting else 'Cancelled. The historical record is retained.')
            return redirect(back)
    from .models import Environment
    environment = Environment.objects.only('name').get(pk=environment_id)
    return render(request, 'inventory/task_cancel.html', {'environment': environment, 'target_label': target_label, 'target_path': target_path, 'title': title, 'description': description, 'back': back, 'available': available})


def reconcile(lane):
    """Called by the lane owner between children, including after restart."""
    if lane in ('all', 'both', 'recalculation'):
        FindingRecalculation.objects.filter(status='cancelling').update(status='cancelled', finished_at=timezone.now())
    if lane in ('all', 'both', 'comparison'):
        SnapshotComparison.objects.filter(status='cancelling').update(status='cancelled')
    if lane in ('all', 'both', 'deletion_preview'):
        DeletionBatch.objects.filter(status='cancelling').update(status='cancelled', finished_at=timezone.now())
