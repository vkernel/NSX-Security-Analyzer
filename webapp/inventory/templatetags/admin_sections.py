from django import template
from django.urls import reverse

register = template.Library()

@register.simple_tag(takes_context=True)
def admin_sections(context):
    request = context['request']
    match = request.resolver_match
    current = match.url_name if match else ''
    namespace = match.namespace if match else ''
    definitions = [
        ('Access', [('Users & access', 'admin:auth_user_changelist'), ('Permission groups', 'admin:auth_group_changelist'), ('Authentication providers', 'authentication-settings')]),
        ('Finding reviews', [('Finding review criteria', 'finding-policy'), ('Review approvals', 'review-approvals')]),
        ('Collections', [('Environments & sync', 'environment-schedules'), ('Freshness & notifications', 'workspace-policy'), ('Retention', 'retention-settings')]),
        ('Operations', [('System health', 'system-health'), ('Audit & diagnostics', 'audit-log')]),
    ]
    result = []
    aliases = {'environment-new':'environment-schedules', 'environment-edit':'environment-schedules',
               'keycloak-settings':'authentication-settings', 'ldap-settings':'authentication-settings'}
    selected = aliases.get(current, current)
    if namespace == 'admin':
        selected = 'admin:auth_group_changelist' if current.startswith('auth_group') else 'admin:auth_user_changelist'
    for title, pages in definitions:
        if not request.user.is_superuser:
            pages = [(label, route) for label, route in pages if route == 'environment-schedules']
        if not pages: continue
        links = [{'label': label, 'url': reverse(route), 'active': route == selected} for label, route in pages]
        result.append({'label': title, 'url': links[0]['url'], 'links': links, 'active': any(link['active'] for link in links)})
    return result
