"""Relay module and section navigation, using the current record's environment."""
from urllib.parse import urlencode

from django import template
from django.urls import reverse

from .admin_sections import admin_sections

register = template.Library()


@register.inclusion_tag('inventory/snapshot_context.html', takes_context=True)
def snapshot_context(context):
    """Metadata only: never load a report to build the evidence selector."""
    from ..models import Snapshot
    request = context['request']
    snapshot = context.get('snapshot')
    if not snapshot or not getattr(snapshot, 'pk', None):
        return {}
    name = request.resolver_match.url_name if request.resolver_match else ''
    switchable = name in {'snapshot', 'segmentation', 'segmentation-pair'}
    history = list(Snapshot.objects.filter(environment_id=snapshot.environment_id)
                   .only('pk', 'environment_id', 'generated_at', 'created_at', 'testing', 'imported', 'needs_review')[:50]) if switchable else []
    if switchable and all(item.pk != snapshot.pk for item in history):
        history.append(snapshot)
    choices = []
    for item in history:
        if name == 'snapshot':
            params = {'browse': '1'} if request.GET.get('browse') == '1' else {}
            url = reverse('snapshot', args=[item.pk])
        else:
            params = request.GET.copy()
            for key in ('page','selected','rule','export','save'):
                params.pop(key, None)
            params['snapshot'] = str(item.pk)
            url = reverse(name, args=[snapshot.environment_id])
        if params:
            url += '?' + (params.urlencode() if hasattr(params, 'urlencode') else urlencode(params, doseq=True))
        choices.append({'snapshot': item, 'url': url, 'selected': item.pk == snapshot.pk, 'latest': item == history[0]})
    return {'request': request, 'context_snapshot': snapshot, 'snapshot_choices': choices,
            'snapshot_is_latest': bool(history and history[0].pk == snapshot.pk),
            'snapshot_can_switch': switchable,
            'snapshot_history_url': reverse('environment', args=[snapshot.environment_id])}


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
    elif current in {'all-collections', 'collection-history', 'collection-result', 'collection-diagnostics', 'collection-detail'}:
        active = 'collections'
    elif current == 'rule-history':
        active = 'firewall'
    elif current in {'my-work', 'findings', 'finding-detail'} or current.startswith('deletion-'):
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
        'overview': [{'label': 'Workspace', 'links': [link('Overview', 'dashboard')]}],
        'environments': [{'label': 'Environments', 'links': [link('All environments', 'environment-directory')]}],
        'inventory': [{'label': 'Snapshot inventory', 'links': [report(label, panel, 'inventory') for label, panel in [('Overview', 'overview'), ('VMs', 'all-vms'), ('Groups', 'all-groups'), ('Services', 'all-services'), ('Tags', 'tags-all'), ('Scopes', 'tags-scopes')]]}],
        'firewall': [{'label': 'Firewall configuration', 'links': [report(label, panel, 'firewall') for label, panel in [('Summary', 'dfw-overview'), ('Policies', 'dfw-policies'), ('Rules', 'dfw-rules'), ('References', 'object-references')]]}],
        'naming': [{'label': 'Naming work', 'links': [scoped('Naming review', 'naming-compliance')]}],
        'segmentation': [{'label': 'Segmentation views', 'links': [scoped('Applications', 'segmentation'), scoped('VM-to-VM rules', 'segmentation-pair')]}],
        'work': [{'label': 'Reviews', 'links': [link('Assigned to me' if staff else 'All findings', 'my-work')]}],
        'collections': [{'label': 'Collections', 'links': [link('All collections', 'all-collections')]}],
        'administration': [{'label': section['label'], 'links': section['links']} for section in admin_sections(context)] if staff else [],
        'personal': [{'label': 'Personal settings', 'links': [link(title, 'website-settings', query={'section': key}) for key, title in [('appearance', 'Appearance'), ('tables', 'Tables'), ('dates', 'Dates & time'), ('updates', 'Live updates'), ('notifications', 'Notifications')]]}],
        'help': [{'label': 'Help & coverage', 'links': [report(label, panel, 'help') for label, panel in [('User guide', 'feature-guide'), ('Coverage & definitions', 'coverage'), ('Tag coverage', 'tags-coverage')]]}],
    }
    if environment:
        groups['environments'].append({'label': environment.name, 'links': [scoped(label, route) for label, route in [('Snapshots', 'environment'), ('Compare snapshots', 'snapshot-comparison'), ('Collection coverage', 'collection-coverage')]] + [link('Findings', 'my-work', query={'queue':'all','environment':environment.pk})]})
        groups['firewall'][0]['links'].append(scoped('Historical activity', 'rule-history'))
        matrix = scoped('Security matrix', 'segmentation'); matrix['url'] += '?tab=matrix'; matrix['active'] = request.GET.get('tab') == 'matrix' and current == 'segmentation'
        groups['segmentation'][0]['links'].append(matrix)
        groups['collections'][0]['links'].append(scoped('History', 'collection-history'))
    # Review queues are always listed so the sidebar can open any queue directly.
    from ..reviews import QUEUES
    queue = context.get('queue') or request.GET.get('queue', '')
    groups['work'][0]['links'] = [dict(link(label, 'my-work', query={'queue': key}), active=current in {'my-work', 'findings'} and queue == key)
                                  for key, label in QUEUES if staff or key not in ('assigned', 'second')]
    if environment and request.user.is_superuser:
        groups['work'][0]['links'].append(scoped('Deletion history', 'deletion-history'))
    if current == 'activity':
        active = 'activity'
    groups['activity'] = [{'label': 'Activity', 'links': [link('Activity', 'activity')]}]
    titles = {'overview': 'Overview', 'activity': 'Activity', 'environments': 'Environments', 'inventory': 'Inventory', 'firewall': 'Firewall', 'naming': 'Naming compliance', 'segmentation': 'Segmentation', 'work': 'Reviews', 'collections': 'Collections', 'administration': 'Administration', 'personal': 'Personal settings', 'help': 'Help & coverage'}
    scope_label = 'All environments' if current in {'dashboard', 'my-work', 'activity', 'all-collections'} and not request.GET.get('environment') else environment.name if environment else 'NSX Security Analyzer'
    canonical = {'naming-compliance':'naming_navigation','findings':'work_navigation',
                 'my-work':'work_navigation','activity':'activity_navigation','website-settings':'settings_navigation',
                 'segmentation':'segmentation_navigation','segmentation-pair':'segmentation_navigation',
                 'environment':'history_navigation','snapshot-comparison':'history_navigation','collection-coverage':'history_navigation'}
    env_query = '?' + urlencode({'environment': environment.pk}) if environment else ''
    if environment:
        segmentation_url = reverse('segmentation', args=[environment.pk])
        if snapshot is not None and getattr(snapshot, 'pk', None) and not snapshot.testing and not snapshot.imported:
            segmentation_url += '?' + urlencode({'snapshot': snapshot.pk})
    else:
        segmentation_url = reverse('segmentation-index')
    on_snapshot = current == 'snapshot'
    definitions = [
        ('overview', None, 'overview', reverse('dashboard')),
        ('activity', None, 'activity', reverse('activity')),
        ('environments', 'Explore', 'environments', reverse('environment-directory')),
        ('inventory', 'Explore', 'inventory', '#overview' if on_snapshot else reverse('workspace-section', args=['inventory']) + env_query),
        ('firewall', 'Explore', 'firewall', '#dfw-overview' if on_snapshot else reverse('workspace-section', args=['firewall']) + env_query),
        ('segmentation', 'Explore', 'segmentation', segmentation_url),
        ('naming', 'Explore', 'tags', reverse('naming-index') + env_query),
        ('work', 'Work', 'work', reverse('my-work')),
        ('collections', 'Work', 'collections', reverse('all-collections')),
    ]
    if staff:
        definitions.append(('administration', 'Work', 'administration', reverse('administration')))
    modules = []
    for key, section, icon, url in definitions:
        pages = []
        sections = groups.get(key, [])
        if key == 'administration':
            pages.append({'label': 'Overview', 'url': reverse('administration'), 'active': current == 'administration'})
        for group in sections:
            if key == 'administration' or len(sections) > 1:
                pages.append({'heading': group['label']})
            pages.extend(group['links'])
        links = [page for page in pages if 'url' in page]
        modules.append({'key': key, 'label': titles[key], 'section': section, 'icon': icon, 'url': url,
                        'active': active == key, 'pages': pages if len(links) > 1 else [],
                        'page_count': len(links)})
    help_url = reverse('workspace-section', args=['help']) + env_query
    # Client-side palette entries: page links and environments only, no inventory data.
    search_index = []
    for module in modules:
        search_index.append({'type': 'Pages', 'label': module['label'], 'meta': module['section'] or 'Workspace', 'url': module['url']})
        heading = ''
        for page in module['pages']:
            if 'heading' in page:
                heading = page['heading']; continue
            if page['url'] != module['url']:
                search_index.append({'type': 'Pages', 'label': module['label'] + ' › ' + page['label'], 'meta': heading or module['label'], 'url': page['url']})
    search_index.append({'type': 'Pages', 'label': 'Help & coverage', 'meta': 'Help', 'url': help_url})
    for env in context.get('navigation_environments', []):
        search_index.append({'type': 'Environments', 'label': env.name, 'meta': 'Snapshots and collections', 'url': reverse('environment', args=[env.pk])})
    return {'active': active, 'title': titles[active], 'scope_label': scope_label, 'environment': environment,
            'modules': modules, 'help_url': help_url, 'search_index': search_index,
            'crumb_page': next((page['label'] for module in modules if module['active'] for page in module['pages'] if page.get('active')), ''),
            'section_expanded': not (getattr(context.get('display_preferences'), 'remember_menus', False) and context.get('interface_state', {}).get('menu:workspace:section-panel') is False),
            'groups': groups[active], 'canonical_template': 'inventory/'+canonical[current]+'.html' if current in canonical else ''}
