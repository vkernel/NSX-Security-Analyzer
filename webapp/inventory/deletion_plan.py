"""Consolidated plan review and explicit, expiring execution authorization."""
import hashlib
import json
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods
from .models import DeletionBatch
from . import deletion, reference_cleanup as cleanup
from .execution_policy import can_execute
from .audit_events import record
from .user_labels import user_label


def fingerprint(batch, items):
    rows = [(item.pk, item.path, cleanup.fingerprint(item)) for item in items]
    return hashlib.sha256(json.dumps([batch.manager, rows], sort_keys=True).encode()).hexdigest()


def maybe_schedule(pk):
    """Never writes to NSX. The normal execution worker repeats authorization/checks."""
    with transaction.atomic():
        batch = DeletionBatch.objects.select_for_update().select_related('environment').get(pk=pk)
        intent = batch.execution_intent
        if intent.get('state') != 'armed' or batch.confirmed_at or batch.status != 'preview_ready': return
        items = list(batch.items.select_related('finding').order_by('pk'))
        actor = get_user_model().objects.filter(pk=intent.get('actor_id')).first()
        try:
            deadline = parse_datetime(intent.get('expires_at', ''))
            if not deadline or timezone.now() >= deadline: raise ValidationError('Execution authorization expired. Confirm again.')
            if not deletion.permitted(actor): raise ValidationError('The authorizing administrator is no longer eligible.')
            if not items or fingerprint(batch, items) != intent.get('fingerprint'): raise ValidationError('The authorized plan changed. Review and authorize it again.')
            if any(item.status not in ('verified', 'cleanup_review') for item in items): raise ValidationError('One or more objects did not pass verification. Review the recorded outcomes.')
            for item in items:
                item.batch = batch
                deletion.guard(batch, item.finding, item)
                if item.cleanup_plan: cleanup.validate_approvals(item, require_complete=False)
            if any(item.status == 'cleanup_review' for item in items): return
            if any(not can_execute(actor, item) for item in items): raise ValidationError('The authorizing administrator cannot execute every object under current execution settings.')
            stale = [item for item in items if not item.verified_at or timezone.now()-item.verified_at >= timedelta(minutes=10)]
            if stale:
                for item in stale:
                    item.verification = {**item.verification, 'refresh_requested': True}
                    item.status = 'cleanup_recheck' if item.cleanup_plan else 'pending'
                    item.message = 'Refreshing verification before the explicitly authorized execution.'
                    item.save(update_fields=['verification','status','message'])
                batch.status = 'preview_queued'; batch.save(update_fields=['status'])
                return
            if not intent.get('ticket'): raise ValidationError('Execution requires a change ticket.')
            batch.ticket = intent['ticket']; batch.confirmed_by = actor; batch.confirmed_actor_id = str(actor.pk)
            batch.confirmed_at = timezone.now(); batch.status = 'queued'
            batch.execution_intent = {**intent, 'state': 'queued'}
            batch.save(update_fields=['ticket','confirmed_by','confirmed_actor_id','confirmed_at','status','execution_intent'])
            record('nsx.deletion_auto_confirmed','DeletionBatch',batch.pk,actor=actor.pk,
                   details={'ticket':batch.ticket,'environment_id':batch.environment_id,'count':len(items),'fingerprint':intent['fingerprint']})
        except (ValidationError, ValueError, TypeError) as exc:
            message = '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'Invalid authorization; review and authorize again.'
            batch.execution_intent = {**intent, 'state':'stopped', 'message':message}
            batch.save(update_fields=['execution_intent'])
            record('nsx.deletion_auto_stopped','DeletionBatch',batch.pk,details={'message':message,'environment_id':batch.environment_id})


@login_required
@require_http_methods(['GET','POST'])
def review(request, pk):
    if not deletion.permitted(request.user): return HttpResponseForbidden('Administrator deletion permission required.')
    error = ''
    with transaction.atomic():
        batch = get_object_or_404(DeletionBatch.objects.select_for_update(of=('self',)).select_related('environment','requested_by'),pk=pk)
        items = list(batch.items.select_related('finding').order_by('pk'))
        digest = fingerprint(batch,items)
        if request.method == 'POST':
            try:
                action = request.POST.get('action')
                if action == 'revoke':
                    if batch.confirmed_at: raise ValidationError('Execution was queued. Cancel the deletion request instead.')
                    if str(batch.execution_intent.get('actor_id')) != str(request.user.pk) and batch.requested_by_id != request.user.pk:
                        raise ValidationError('Only the authorizing administrator or requester can revoke this authorization.')
                    batch.execution_intent = {**batch.execution_intent,'state':'revoked'}
                    batch.save(update_fields=['execution_intent'])
                    record('nsx.deletion_auto_revoked','DeletionBatch',batch.pk,actor=request.user.pk)
                else:
                    if action not in ('approve','authorize'): raise ValidationError('Choose a supported action.')
                    if batch.status != 'preview_ready' or batch.confirmed_at: raise ValidationError('This plan is not available for review.')
                    if request.POST.get('fingerprint') != digest: raise ValidationError('The plan changed. Reload and review the exact changes.')
                    if not items or any(item.status not in ('verified','cleanup_review') for item in items): raise ValidationError('Resolve blocked or unfinished items before authorizing the whole plan.')
                    pending = []
                    for item in items:
                        item.batch = batch
                        deletion.guard(batch,item.finding,item)
                        if item.cleanup_plan:
                            cleanup.validate_approvals(item, require_complete=item.status=='verified')
                            if item.status=='cleanup_review' and not any(str(d.get('actor_id'))==str(request.user.pk) for d in item.cleanup_approvals): pending.append(item)
                    if action=='approve' and (not pending or request.POST.get('approve')!='yes'):
                        raise ValidationError('Explicitly approve the displayed firewall changes. You cannot approve the same plan twice.')
                    if action=='authorize':
                        ticket=request.POST.get('ticket','').strip()
                        if request.POST.get('execute_after_approval')!='yes' or not ticket or len(ticket)>255:
                            raise ValidationError('A change ticket and explicit authorization for permanent deletion are required.')
                        participant = batch.requested_by_id == request.user.pk or all(any(str(d.get('actor_id')) == str(request.user.pk) for d in item.cleanup_approvals + list(item.approvals.values()) if isinstance(d,dict)) for item in items)
                        if not participant: raise ValidationError('Only a requester or recorded approver can authorize this plan.')
                        batch.execution_intent={'state':'armed','actor_id':str(request.user.pk),'actor_name':user_label(request.user),
                            'ticket':ticket,'fingerprint':digest,'expires_at':(timezone.now()+timedelta(hours=1)).isoformat()}
                        batch.save(update_fields=['execution_intent'])
                        record('nsx.deletion_auto_authorized','DeletionBatch',batch.pk,actor=request.user.pk,details=batch.execution_intent)
                    else:
                        for item in pending:
                            item.cleanup_approvals = item.cleanup_approvals + [{'actor_id':str(request.user.pk),'actor_name':user_label(request.user),'fingerprint':cleanup.fingerprint(item),'at':timezone.now().isoformat()}]
                            if len({str(d['actor_id']) for d in item.cleanup_approvals}) >= cleanup.requirement(item.finding):
                                cleanup.validate_approvals(item)
                                item.status='verified'
                                item.message='Cleanup approvals complete. Confirm execution or refresh expired verification.'
                            item.save(update_fields=['cleanup_approvals','status','message'])
                            record('nsx.deletion_cleanup_approved','DeletionItem',item.pk,actor=request.user.pk,details={'batch':str(batch.pk),'fingerprint':cleanup.fingerprint(item),'environment_id':batch.environment_id})
                maybe_schedule(batch.pk)
                return redirect('deletion-plan',pk=pk)
            except ValidationError as exc:
                error='; '.join(exc.messages)
    for item in items:
        item.plan_summary=cleanup.summary(item.cleanup_plan) if item.cleanup_plan else 'Delete this object after fresh execution checks.'
        item.approval_count=len({str(d.get('actor_id')) for d in item.cleanup_approvals})
        item.approval_required=cleanup.requirement(item.finding) if item.cleanup_plan and item.finding else 0
    response=render(request,'inventory/deletion_plan.html',{'batch':batch,'items':items,'fingerprint':digest,'error':error,
        'can_review':batch.status=='preview_ready' and not batch.confirmed_at,
        'has_pending':any(item.status=='cleanup_review' for item in items)})
    response['Cache-Control']='no-store'
    return response
