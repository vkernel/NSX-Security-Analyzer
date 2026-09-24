from .models import UserPreferences


def preferences(request):
    if not hasattr(request, '_workspace_preferences'):
        saved = (UserPreferences.objects.filter(user=request.user).first()
                 if request.user.is_authenticated else None)
        request._workspace_preferences = saved or UserPreferences()
    return request._workspace_preferences


def display_preferences(request):
    from .models import Environment
    environments = list(Environment.objects.all()) if request.user.is_authenticated else []
    selected_id = request.session.get('selected_environment')
    match = request.resolver_match
    if match and match.kwargs.get('pk') and match.url_name in ('environment','environment-edit','collection-history','rule-history'):
        selected_id = match.kwargs['pk']
    selected = next((env for env in environments if env.pk == selected_id), None)
    if selected is None:
        selected = next((env for env in environments if env.pk == preferences(request).preferred_environment_id), None)
    if selected is None and environments:
        selected = environments[0]
    return {'display_preferences': preferences(request), 'interface_state': preferences(request).interface_state,
            'navigation_environments': environments, 'selected_environment': selected}
