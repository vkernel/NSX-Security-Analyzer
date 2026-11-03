"""Relay module and section navigation, using the current record's environment."""
from urllib.parse import urlencode

from django import template
from django.urls import reverse

from .admin_sections import admin_sections

register = template.Library()


@register.simple_tag(takes_context=True)
def relay_sections(context):
    request = context['request']
    match = request.resolver_match
    current = (match.url_name or '') if match else ''
    environment = context.get('environment') or context.get('selected_environment')
    for key in ('snapshot', 'batch', 'job', 'finding'):
        record = context.get(key)
        if record is not None and getattr(record, 'environment_id', None):
            environment = record.environment
            break
    if environment is not None and environment.pk is None:
        environment = context.get('selected_environment')
    snapshot = context.get('snapshot')
    staff = request.user.is_staff
    active = 'overview'
    if request.path.startswith('/administration/') or (match and match.namespace == 'admin') or current in {'administration', 'environment-new', 'environment-edit'}:
        active = 'administration'
    elif current.startswith('segmentation'):
        active = 'segmentation'
    elif current in {'naming-compliance', 'naming-index'}:
        active = 'naming'
    elif current in {'all-collections', 'collection-history', 'collection-result', 'collection-diagnostics'}:
        active = 'collections'
    elif current == 'rule-history':
        active = 'firewall'
    elif current == 'my-work' or current.startswith('deletion-'):
        active = 'work'
    elif current in {'snapshot', 'impact-preview', 'report-content'}:
        active = 'inventory'
    elif current in {'environment-directory', 'environment', 'findings', 'finding-detail', 'snapshot-comparison', 'collection-coverage'}:
        active = 'environments'
    elif current == 'website-settings' or current == 'password-change':
        active = 'personal'
    elif current == 'workspace-section':
        section = match.kwargs.get('section')
        active = 'firewall' if section in {'firewall', 'rules', 'policies'} else 'help' if section == 'help' else 'environments' if section == 'environments' else 'inventory'

    def link(label, route, args=None, query=None, selected=None):
        url = reverse(route, args=args)
        if query:
            url += '?' + urlencode(query)
        return {'label': label, 'url': url, 'active': current == (selected or route) and (not query or all(request.GET.get(key, 'appearance' if key == 'section' else '') == str(value) for key, value in query.items()))}

    def scoped(label, route):
        return link(label, route, [environment.pk]) if environment else link('Choose an environment', 'environment-directory')

    def report(label, panel, section):
        url = reverse('snapshot', args=[snapshot.pk]) + '#' + panel if snapshot else reverse('workspace-section', args=[section]) + '?' + urlencode({'environment': environment.pk, 'panel': panel} if environment else {'panel': panel})
        return {'label': label, 'url': url, 'active': current == 'snapshot' and panel == 'overview'}

    groups = {
        'overview': [{'label': 'Workspace', 'links': [link('Overview', 'dashboard'), link('Activity', 'activity')]}],
        'environments': [{'label': 'Environments', 'links': [link('All environments', 'environment-directory')]}],
        'inventory': [{'label': 'Snapshot inventory', 'links': [report(label, panel, 'inventory') for label, panel in [('Snapshot overview', 'overview'), ('VMs', 'all-vms'), ('Groups', 'all-groups'), ('Services', 'all-services'), ('Tags', 'tags-all'), ('Scopes', 'tags-scopes')]]}],
        'firewall': [{'label': 'Firewall configuration', 'links': [report(label, panel, 'firewall') for label, panel in [('Summary', 'dfw-overview'), ('Policies', 'dfw-policies'), ('Rules', 'dfw-rules')]]}],
        'naming': [{'label': 'Naming work', 'links': [scoped('Naming review', 'naming-compliance')]}],
        'segmentation': [{'label': 'Segmentation views', 'links': [scoped('Applications overview', 'segmentation'), scoped('VM-to-VM rules', 'segmentation-pair')]}],
        'work': [{'label': 'Review work', 'links': [link('My work', 'my-work')]}] if staff else [],
        'collections': [{'label': 'Collections', 'links': [link('All collections', 'all-collections')]}],
        'administration': [{'label': section['label'], 'links': section['links']} for section in admin_sections(context)] if staff else [],
        'personal': [{'label': 'Personal settings', 'links': [link(title, 'website-settings', query={'section': key}) for key, title in [('appearance', 'Appearance'), ('tables', 'Tables'), ('dates', 'Dates & time'), ('updates', 'Live updates'), ('notifications', 'Notifications')]]}],
        'help': [{'label': 'Help & coverage', 'links': [report(label, panel, 'help') for label, panel in [('User guide', 'feature-guide'), ('Audit coverage', 'coverage'), ('Tag coverage', 'tags-coverage')]]}],
    }
    if environment:
        groups['environments'].append({'label': environment.name, 'links': [scoped(label, route) for label, route in [('Snapshots', 'environment'), ('Compare snapshots', 'snapshot-comparison'), ('Finding reviews', 'findings'), ('Collection coverage', 'collection-coverage')]]})
        groups['firewall'][0]['links'].append(scoped('Historical activity', 'rule-history'))
        matrix = scoped('Security matrix', 'segmentation'); matrix['url'] += '?tab=matrix'; matrix['active'] = request.GET.get('tab') == 'matrix' and current == 'segmentation'
        groups['segmentation'][0]['links'].append(matrix)
        if staff:
            groups['work'][0]['links'] += [scoped('Finding reviews', 'findings'), scoped('Deletion history', 'deletion-history')]
        groups['collections'][0]['links'].append(scoped('Environment history', 'collection-history'))
    titles = {'overview': 'Overview', 'environments': 'Environments', 'inventory': 'Inventory', 'firewall': 'Firewall', 'naming': 'Naming compliance', 'segmentation': 'Segmentation', 'work': 'My work', 'collections': 'Collections', 'administration': 'Administration', 'personal': 'Personal settings', 'help': 'Help & coverage'}
    scope_label = 'All environments' if current in {'dashboard', 'my-work', 'activity', 'all-collections'} and not request.GET.get('environment') else environment.name if environment else 'NSX Security Analyzer'
    return {'active': active, 'title': titles[active], 'scope_label': scope_label, 'environment': environment, 'groups': groups[active]}
