from . import keycloak_settings, ldap_settings


def settings_page(request):
    if request.GET.get('provider') == 'ldap':
        return ldap_settings.settings_page(request)
    return keycloak_settings.settings_page(request)
