from .models import UserPreferences


def preferences(request):
    if not hasattr(request, '_workspace_preferences'):
        saved = (UserPreferences.objects.filter(user=request.user).first()
                 if request.user.is_authenticated else None)
        request._workspace_preferences = saved or UserPreferences()
    return request._workspace_preferences


def display_preferences(request):
    from .models import Environment
    environments = list(Environment.objects.only("id", "name")) if request.user.is_authenticated else []
    selected_id = request.session.get('selected_environment')
    match = request.resolver_match
    if match and match.kwargs.get('pk') and match.url_name in ('environment','environment-edit','collection-history','rule-history','snapshot-comparison','collection-coverage','findings','finding-detail','deletion-history','deletion-selection'):
        selected_id = match.kwargs['pk']
    explicit = request.GET.get('environment', '')
    if explicit.isdecimal() and len(explicit) < 19:
        selected_id = int(explicit)
    if request.headers.get('X-Environment-Check') != '1' and any(env.pk == selected_id for env in environments):
        request.session['selected_environment'] = selected_id
    selected = next((env for env in environments if env.pk == selected_id), None)
    if selected is None:
        selected = next((env for env in environments if env.pk == preferences(request).preferred_environment_id), None)
    if selected is None and environments:
        selected = environments[0]
    return {'display_preferences': preferences(request), 'interface_state': preferences(request).interface_state,
            'navigation_environments': environments, 'selected_environment': selected}


def table_page_size(request, params=None):
    """Bounded table size, remembered in this signed-in browser session."""
    params = request.GET if params is None else params
    value = params.get('page_size', '')
    allowed = (10, 20, 25, 50, 100)
    if str(value) in {str(n) for n in allowed}:
        size = int(value)
        if hasattr(request, 'session'):
            request.session['table_page_size'] = size
        return size
    saved = getattr(request, 'session', {}).get('table_page_size')
    return saved if saved in allowed else preferences(request).page_size
