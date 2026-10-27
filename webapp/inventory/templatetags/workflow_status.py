from django import template
register = template.Library()

@register.inclusion_tag('components/workflow_status.html', takes_context=True)
def workflow_status(context):
    request = context['request']
    name = request.resolver_match.url_name
    queue = context.get('queue') or context.get('review') or request.GET.get('review', '')
    if queue == 'second_review': queue = 'second'
    finding = context.get('finding')
    title, message = 'Review current work', 'Open an item to review its evidence and available actions. Rejected and completed reviews are available in their history tabs.'
    if finding:
        title = finding.get_workflow_state_display()
        message = {'ready': 'Approval is complete. Review the deletion preview before confirming any NSX changes.',
            'decommissioned': 'This review is closed. Saved evidence and decisions remain available below.',
            'rejected': 'This review was rejected. Read the recorded reason before starting a new review.',
            'unassigned': 'Assign a reviewer before requesting approval.'}.get(finding.workflow_state, 'Review the evidence and recorded decisions before taking the next available action.')
    elif name in ('my-work', 'findings'):
        title, message = {'second': ('Awaiting second approval', 'A different eligible reviewer must inspect the evidence and record their decision.'),
            'ready': ('Ready for decommissioning', 'Select objects to preview deletion. No NSX changes occur until execution is confirmed.'),
            'rejected': ('Rejected reviews', 'These are historical decisions, not pending approval tasks.'),
            'decommissioned': ('Completed reviews', 'Review saved outcomes and evidence; no action is pending.'),
            'assigned': ('Your assigned reviews', 'Open a finding, inspect the evidence and record your decision.')}.get(queue, (title, message))
    elif name == 'activity':
        title = {'running': 'Running tasks', 'audit': 'Activity history'}.get(context.get('tab'), 'Active and recent activity')
        message = 'Open a task to see progress and the next available action.' if context.get('tab') == 'running' else 'Past decisions and outcomes are shown for reference. Use Running tasks for active work.'
    elif name == 'collection-diagnostics':
        job = context['job']
        title = job.get_status_display()
        message = 'Follow collection progress below. Previous snapshots remain available.' if job.status in ('queued', 'running') else 'Collection processing finished. Review coverage warnings and diagnostics below before using its evidence.'
    elif name in ('all-collections', 'collection-history'):
        title, message = 'Collection status', 'Open a collection to inspect progress or results. Completed runs remain in this history.'
    elif name == 'deletion-history':
        title, message = 'Deletion history', 'Open a request to inspect each object outcome. Completed, rejected and cancelled entries are historical records.'
    return {'status_title': title, 'status_message': message}
