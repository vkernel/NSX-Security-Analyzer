"""Execution eligibility is separate from review approval and NSX credentials."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from .approval_modes import category
from .usability import policy

MODES = [('requester', 'Requester only'), ('approvers', 'Either approver'), ('participants', 'Requester or either approver')]


def mode(kind, settings):
    key = category(kind)
    return (getattr(settings, key + '_execution', '') if key else '') or settings.execution_mode


def eligible_ids(item):
    from .deletion import approvals_valid, permitted
    from .reference_cleanup import validate_approvals
    finding = item.finding
    if not finding: return set()
    approvals_valid(finding)
    if item.cleanup_plan:
        validate_approvals(item)
        approvers = {str(row['actor_id']) for row in item.cleanup_approvals}
    else:
        stages = ['owner', 'second'] if finding.required_approvals == 2 else ['owner']
        approvers = {str(finding.approvals[stage]['actor_id']) for stage in stages}
    requester = {str(item.batch.requested_by_id)} if item.batch.requested_by_id else set()
    settings = policy()
    modes = [mode(finding.kind, settings)]
    if item.cleanup_plan: modes.append(mode('disabled', settings))
    # Intersection handles requester-only versus approver-only without weakening either.
    allowed = requester | approvers
    for value in modes:
        allowed &= {'requester': requester, 'approvers': approvers, 'participants': requester | approvers}.get(value, set())
    return {user.pk for user in get_user_model().objects.filter(pk__in=allowed) if permitted(user)}


def can_execute(user, item):
    try:
        return bool(user and user.pk in eligible_ids(item))
    except (ValidationError, KeyError, ValueError):
        return False


def describe(user, item):
    from .user_labels import user_label
    try:
        ids = eligible_ids(item)
    except (ValidationError, KeyError, ValueError):
        return 'Execution eligibility requires valid current approvals and evidence.'
    if user.pk in ids: return 'You can confirm execution.'
    names = [user_label(actor) for actor in get_user_model().objects.filter(pk__in=ids).order_by('pk')]
    return 'Waiting for confirmation from ' + ' or '.join(names) + '.' if names else 'No authorized executor matches the current group/firewall execution policies. Review execution settings or obtain the required approvals.'


def executor(batch):
    # A removed executor must never fall back to the requester on new confirmations.
    return batch.confirmed_by if batch.confirmed_actor_id else batch.requested_by
