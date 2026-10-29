"""Explicit navigation scopes: lists switch; immutable records identify their owner."""
from urllib.parse import urlencode
from django import template
from django.urls import reverse
register = template.Library()

@register.inclusion_tag('inventory/environment_scope.html', takes_context=True)
def environment_scope(context):
    request = context['request']
    match = request.resolver_match
    name = match.url_name if match else ''
    environments = context.get('navigation_environments', [])
    environment = context.get('environment')
    for key in ('batch', 'snapshot', 'job', 'finding'):
        obj = context.get(key)
        if obj is not None and getattr(obj, 'environment_id', None):
            environment = obj.environment
            break
    lists = {'naming-compliance', 'environment', 'findings', 'collection-history', 'rule-history', 'deletion-history', 'collection-coverage', 'snapshot-comparison'}
    aggregate = name in {'dashboard', 'my-work', 'activity', 'all-collections'}
    browsing = name == 'snapshot' and request.GET.get('browse') == '1'
    switchable = aggregate or name in lists or browsing or name == 'workspace-section' and match.kwargs.get('section') != 'help'
    if not switchable:
        return {'fixed_environment': environment}
    selected = str(environment.pk) if environment else str(context.get('selected_environment').pk) if context.get('selected_environment') else ''
    if aggregate: selected = request.GET.get('environment', '')
    params = {key: value for key, value in request.GET.items() if key in {'q', 'sort', 'review', 'queue', 'status', 'kind', 'view', 'tab', 'type', 'actor', 'start', 'end', 'outcome'}}
    options = []
    for env in ([None] if aggregate else []) + list(environments):
        query = dict(params)
        if aggregate:
            if env: query['environment'] = env.pk
            url = reverse(name)
        elif browsing or name == 'workspace-section':
            url = reverse('workspace-section', args=[match.kwargs.get('section', 'inventory')])
            query['environment'] = env.pk
        else:
            url = reverse(name, args=[env.pk])
        if query: url += '?' + urlencode(query)
        options.append({'id': str(env.pk) if env else '', 'name': env.name if env else 'All environments', 'url': url, 'selected': selected == (str(env.pk) if env else '')})
    return {'scope_options': options, 'scope_browsing': browsing}
