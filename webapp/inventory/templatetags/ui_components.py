"""Shared presentation only: never infer authorization from a status label."""
from django import template
register = template.Library()
STATUSES = {
    'cancelled': ('Cancelled', 'neutral'), 'cancelling': ('Cancellation requested', 'info'),
    'queued': ('Waiting for worker', 'info'), 'pending': ('Pending', 'neutral'),
    'running': ('Running', 'info'), 'checking': ('Verifying', 'info'),
    'succeeded': ('Completed', 'success'), 'completed': ('Completed', 'neutral'),
    'failed': ('Failed', 'error'), 'uncertain': ('Needs verification', 'warning'),
    'blocked': ('Blocked', 'warning'), 'interrupted': ('Interrupted', 'warning'),
    'deleted': ('Deleted', 'success'), 'already_absent': ('Already absent', 'neutral'),
    'verified': ('Ready to confirm', 'success'), 'cleanup_review': ('Awaiting cleanup approval', 'warning'),
    'cleanup_recheck': ('Revalidating', 'info'), 'cleanup_rejected': ('Cleanup rejected', 'warning'),
    'second_review': ('Awaiting second approval', 'warning'), 'ready': ('Ready for decommissioning', 'success'),
    'owner_review': ('Owner review', 'info'), 'unassigned': ('Unassigned', 'neutral'),
    'rejected': ('Rejected', 'warning'), 'decommissioned': ('Decommissioned', 'success'),
    'suppressed': ('Suppressed', 'neutral'), 'sending': ('Deleting', 'info'),
    'validating': ('Verifying', 'info'), 'preview_ready': ('Preview ready', 'neutral'),
    'preview_queued': ('Waiting for preview worker', 'info'),
}

@register.inclusion_tag('components/status_badge.html')
def status_badge(value, label=None):
    key = str(value or '')
    default, tone = STATUSES.get(key, (key.replace('_', ' ').capitalize() or 'Not recorded', 'neutral'))
    return {'label': label or default, 'tone': tone}
