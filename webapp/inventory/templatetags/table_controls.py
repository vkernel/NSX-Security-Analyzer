from django import template
register = template.Library()


@register.inclusion_tag('components/table_footer.html', takes_context=True)
def table_footer(context, page, page_key='page', total=None):
    request = context['request']
    if not isinstance(total, int): total = None
    def url(**changes):
        params = request.GET.copy()
        for key, value in changes.items():
            if value is None: params.pop(key, None)
            else: params[key] = value
        return '?' + params.urlencode()
    sizes = sorted({25, 50, 100, page.paginator.per_page})
    return {'page': page, 'total': total, 'sizes': [{'value': n, 'url': url(page=None, gap_page=None, page_size=n)} for n in sizes],
            'previous': url(**{page_key: page.previous_page_number()}) if page.has_previous() else '',
            'next': url(**{page_key: page.next_page_number()}) if page.has_next() else ''}
