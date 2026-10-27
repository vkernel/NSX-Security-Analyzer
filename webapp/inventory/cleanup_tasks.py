"""Discoverable review tasks backed by immutable deletion cleanup plans."""
from django.urls import reverse
from .models import DeletionItem
from .deletion import permitted
from .reference_cleanup import requirement, summary
from .execution_policy import can_execute


def tasks(user, *, environment=None, query='', queue=None):
    if not permitted(user) or queue in ('unassigned', 'rejected', 'decommissioned', 'suppressed'): return []
    rows = DeletionItem.objects.filter(cleanup_results=[]).exclude(cleanup_plan=[]).filter(
        status__in=['cleanup_review', 'cleanup_recheck', 'verified'],
        batch__status__in=['preview_ready', 'preview_queued', 'checking']).select_related('batch__environment', 'batch__requested_by', 'finding').order_by('-pk')
    if queue == 'second':
        rows = rows.filter(status='cleanup_review', batch__status='preview_ready', batch__confirmed_at__isnull=True).exclude(cleanup_approvals=[])
    elif queue in ('assigned', 'ready'):
        rows = rows.filter(batch__confirmed_at__isnull=True)
        if queue == 'assigned': rows = rows.filter(batch__requested_by=user)
    if queue == 'ready': rows = rows.filter(status='verified')
    if environment:
        if not str(environment).isdecimal(): return []
        rows = rows.filter(batch__environment_id=environment)
    if query:
        from django.db.models import Q
        rows = rows.filter(Q(name__icontains=query) | Q(batch__environment__name__icontains=query))
    result = []
    seen = set()
    for item in rows[:500]:
        actors = {str(d.get('actor_id')) for d in item.cleanup_approvals}
        mine = item.batch.requested_by_id == user.pk
        eligible = item.status == 'verified' and can_execute(user, item)
        review = item.status == 'cleanup_review' and item.batch.status == 'preview_ready' and not item.batch.confirmed_at
        second = review and bool(actors) and str(user.pk) not in actors
        if queue == 'second' and not second: continue
        if queue == 'assigned' and not mine: continue
        if queue == 'ready' and not (eligible and item.status == 'verified' and not item.batch.confirmed_at): continue
        if queue is None and not (mine or second or eligible): continue
        if item.batch.confirmed_at: continue
        identity = (item.batch.environment_id, item.path)
        if identity in seen: continue
        seen.add(identity)
        label = {'cleanup_review': 'Awaiting second approval' if actors else 'Awaiting cleanup approval',
                 'cleanup_recheck': 'Revalidating', 'verified': 'Ready to confirm',
                 'cleanup_rejected': 'Rejected', 'blocked': 'Needs attention', 'failed': 'Needs attention', 'uncertain': 'Needs attention'}[item.status]
        if item.status == 'verified':
            from datetime import timedelta
            from django.utils import timezone
            if not item.verified_at or timezone.now() - item.verified_at >= timedelta(minutes=10):
                label = 'Confirmation expired — revalidate'
        result.append({'item': item, 'label': label, 'second': second, 'summary': summary(item.cleanup_plan),
                       'approvals': len(actors), 'required': requirement(item.finding) if item.finding else 2,
                       'action_label': 'Revalidate plan' if label == 'Confirmation expired — revalidate' else 'Review second approval' if second else 'View progress' if item.status == 'cleanup_recheck' or str(user.pk) in actors and review else 'Confirm execution' if item.status == 'verified' and eligible else 'Review plan',
                       'url': reverse('deletion-reference-cleanup', args=[item.pk]),
                       'first_approver': item.cleanup_approvals[0].get('actor_name', '') if actors else '',
                       'at': item.finished_at or item.verified_at or item.batch.created_at})
    return result
