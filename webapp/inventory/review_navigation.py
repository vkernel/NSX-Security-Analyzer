"""Bounded review navigation; return links can only target known local queues."""
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
    if parts.path == reverse('my-work') and not request.user.is_staff:
        return fallback
    return parts.path + ('?' + parts.query if parts.query else '')


def navigation_url(finding, return_to):
    return reverse('finding-detail', args=[finding.environment_id, finding.pk]) + '?' + urlencode({'return_to': return_to})


def navigation(request, environment, finding, return_to):
    # Reuse the exact list filters/order. Only fetch the originating page and
    # its immediate neighbours, never materialize the entire finding inventory.
    parts = urlsplit(return_to)
    if parts.path != reverse('findings', args=[environment.pk]):
        return {}
    from .history_views import filtered_findings
    params = QueryDict(parts.query)
    rows, _, _ = filtered_findings(request, environment, params)
    paginator = Paginator(rows, preferences(request).page_size)
    page = paginator.get_page(params.get('page'))
    entries = list(page.object_list)
    index = next((i for i, item in enumerate(entries) if item.pk == finding.pk), None)
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
        result[name] = navigation_url(target, parts.path + '?' + query.urlencode())
    return result
