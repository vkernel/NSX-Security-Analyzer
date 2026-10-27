"""Approval requirements by finding category, with a workspace fallback."""
CATEGORIES = {'groups': ('Groups (including VDI)', ('unused', 'empty_group', 'membership')),
              'services': ('Custom services', ('unused_service',)),
              'rules': ('Firewall rules', ('zero_hits', 'disabled', 'statistics')),
              'policies': ('Firewall policies', ('empty_policy', 'policy'))}


def category(kind):
    return next((key for key, (_, kinds) in CATEGORIES.items() if kind in kinds), None)


def required(kind, policy):
    key = category(kind)
    return (getattr(policy, key + '_approvals', 0) if key else 0) or policy.required_approvals


def source(kind, policy):
    key = category(kind)
    return CATEGORIES[key][0] + ' override' if key and getattr(policy, key + '_approvals', 0) else 'Workspace default'


def preview(policy):
    from .models import Finding
    from .execution_policy import mode, MODES
    result = []
    for key, (label, kinds) in CATEGORIES.items():
        effective = required(kinds[0], policy)
        count = Finding.objects.filter(kind__in=kinds).exclude(workflow_state__in=['rejected', 'decommissioned']).exclude(required_approvals=effective).count()
        result.append({'label': label, 'effective': 'Single approval' if effective == 1 else 'Four-eyes', 'source': source(kinds[0], policy), 'count': count, 'execution': dict(MODES)[mode(kinds[0], policy)]})
    return result
