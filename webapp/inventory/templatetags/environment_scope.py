"""Explicit navigation scopes: lists switch; immutable records identify their owner."""
from urllib.parse import urlencode
from django import template
from django.urls import reverse
from django.db.models import OuterRef, Subquery
from ..models import Environment, Snapshot
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
    lists = {'segmentation-pair', 'segmentation', 'naming-compliance', 'environment', 'findings', 'collection-history', 'rule-history', 'deletion-history', 'collection-coverage', 'snapshot-comparison'}
    aggregate = name in {'dashboard', 'my-work', 'findings', 'activity', 'all-collections'}
    browsing = name == 'snapshot' and request.GET.get('browse') == '1'
    switchable = aggregate or name in lists or browsing or name == 'workspace-section' and match.kwargs.get('section') != 'help'
    if not switchable:
        return {'fixed_environment': environment}
    selected = str(environment.pk) if environment else str(context.get('selected_environment').pk) if context.get('selected_environment') else ''
    if aggregate and name!='findings': selected = request.GET.get('environment', '')
    # Session-scoped shortcuts never retain names, URLs or inaccessible objects.
    available = {str(env.pk) for env in environments}
    stored = request.session.get('recent_environment_ids', [])
    recent = list(dict.fromkeys(str(pk) for pk in stored if str(pk) in available))[:5] if isinstance(stored, list) else []
    if selected in available and request.method == 'GET' and not request.headers.get('X-Requested-With') and request.headers.get('X-Environment-Check') != '1':
        recent = [selected] + [pk for pk in recent if pk != selected]
    recent = recent[:5]
    if stored != recent:
        request.session['recent_environment_ids'] = recent
    ranks = {pk: index + 1 for index, pk in enumerate(recent) if pk != selected}
    params = {key: value for key, value in request.GET.items() if key in {'q', 'sort', 'review', 'queue', 'status', 'kind', 'view', 'tab', 'type', 'actor', 'start', 'end', 'outcome'}}
    if name in ('my-work','findings'):
        params.update({key:value for key,value in request.GET.items() if key in ('owner','direction','task_type','page_size')})
        params['queue']=context.get('queue','all')
        if params['queue']!='all':
            params.pop('owner',None);params.pop('review',None)
    latest_ids = {}
    if browsing or name == 'workspace-section':
        latest_query = Snapshot.objects.filter(environment_id=OuterRef('pk')).order_by('-generated_at','-created_at').values('pk')[:1]
        latest_ids = dict(Environment.objects.filter(pk__in=[e.pk for e in environments]).annotate(latest_snapshot=Subquery(latest_query)).values_list('pk','latest_snapshot'))
    options = []
    for env in ([None] if aggregate else []) + list(environments):
        query = dict(params)
        if aggregate:
            if env: query['environment'] = env.pk
            url = reverse('my-work' if name=='findings' else name)
        elif browsing or name == 'workspace-section':
            latest = latest_ids.get(env.pk)
            section = match.kwargs.get('section', 'inventory')
            if latest and section in {'inventory','firewall','rules','policies','services','tags','scopes','help'}:
                url = reverse('snapshot', args=[latest])
                query = {'browse':'1'}
            else:
                url = reverse('workspace-section', args=[section])
                query['environment'] = env.pk
        else:
            url = reverse(name, args=[env.pk])
        if str(env.pk if env else '') != selected and any(request.GET.get(key) for key in ('snapshot','source','destination','groups','policy','owner')):
            query['context_changed'] = '1'
        if query: url += '?' + urlencode(query)
        options.append({'id': str(env.pk) if env else '', 'name': env.name if env else 'All environments', 'url': url, 'selected': selected == (str(env.pk) if env else ''), 'recent_rank': ranks.get(str(env.pk)) if env else None})
    return {'scope_options': options, 'scope_browsing': browsing or name == 'workspace-section'}
