"""Bounded review navigation; return links can only target known local queues."""
from .preferences import table_page_size
from urllib.parse import urlencode, urlsplit
from django.http import QueryDict
from django.urls import reverse
from django.core.paginator import Paginator
from .preferences import preferences


def return_url(request, environment_id):
    fallback = reverse('findings', args=[environment_id])
    value = request.GET.get('return_to', '')
    if len(value) > 4096 or any(char in value for char in ('\\', '\r', '\n')):
        return fallback
    try:
        parts = urlsplit(value)
    except ValueError:
        return fallback
    if parts.scheme or parts.netloc or parts.path not in (fallback, reverse('my-work')):
        return fallback
    return parts.path + ('?' + parts.query if parts.query else '')


def navigation_url(finding, return_to):
    return reverse('finding-detail', args=[finding.environment_id, finding.pk]) + '?' + urlencode({'return_to': return_to})


def navigation(request, environment, finding, return_to):
    # Reuse the exact list filters/order. Only fetch the originating page and
    # its immediate neighbours, never materialize the entire finding inventory.
    parts = urlsplit(return_to)
    if parts.path not in (reverse('findings', args=[environment.pk]), reverse('my-work')):
        return {}
    from .reviews import listing, queue_for
    from .cleanup_tasks import tasks
    from .models import Environment
    params = QueryDict(parts.query).copy()
    legacy = parts.path != reverse('my-work')
    params.setdefault('sort','name' if legacy else 'oldest')
    params.setdefault('direction','desc' if params['sort']=='updated' else 'asc')
    selected = str(environment.pk) if legacy else params.get('environment','')
    if selected and (not selected.isdecimal() or len(selected)>18): return {'changed':True}
    scoped = Environment.objects.filter(pk=selected).first() if selected else None
    if selected and scoped is None: return {'changed':True}
    # Parse the originating queue without changing the detail request's params.
    from types import SimpleNamespace
    queue = queue_for(SimpleNamespace(GET=params,user=request.user), legacy=legacy)
    cleanup = tasks(request.user,environment=selected or None,queue='all')
    rows, _, _ = listing(request,scoped,params,queue,cleanup)
    paginator = Paginator(rows, table_page_size(request, params))
    page = paginator.get_page(params.get('page'))
    entries = list(page.object_list)
    index = next((i for i, item in enumerate(entries) if item['task_kind']=='finding' and item['task_id'] == finding.pk), None)
    if index is None:
        return {'changed': True}
    result = {}
    for name, offset in (('previous', -1), ('next', 1)):
        position, number = index + offset, page.number
        if 0 <= position < len(entries):
            target = entries[position]
        elif position < 0 and page.has_previous():
            number -= 1
            target = list(paginator.page(number).object_list)[-1]
        elif position >= len(entries) and page.has_next():
            number += 1
            target = list(paginator.page(number).object_list)[0]
        else:
            continue
        query = params.copy(); query['page'] = number
        back = parts.path + '?' + query.urlencode()
        if target['task_kind']=='cleanup':
            result[name] = reverse('deletion-reference-cleanup',args=[target['task_id']])+'?'+urlencode({'return_to':back})
        else:
            from .models import Finding
            record = Finding.objects.only('pk','environment_id').filter(pk=target['task_id']).first()
            if record: result[name] = navigation_url(record, back)
    return result
