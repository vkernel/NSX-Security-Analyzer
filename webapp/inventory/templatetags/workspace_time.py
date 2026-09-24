from datetime import timezone as utc_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django import template
from django.utils.html import format_html
from inventory.preferences import preferences

register = template.Library()


@register.simple_tag(takes_context=True)
def user_time(context, value, plain=False):
    if not value:
        return '—'
    saved = preferences(context['request'])
    try:
        zone = ZoneInfo(saved.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo('UTC')
    patterns = {'readable': '%d %b %Y, %H:%M', 'iso': '%Y-%m-%d %H:%M', 'day-first': '%d/%m/%Y %H:%M', 'month-first': '%m/%d/%Y %H:%M'}
    stamp = value.astimezone(zone)
    text = stamp.strftime(patterns.get(saved.date_format, patterns['readable'])) + ' ' + stamp.tzname()
    if plain:
        return text
    return format_html('<time datetime="{}" title="{}">{}</time>', value.isoformat(), value.astimezone(utc_timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'), text)
