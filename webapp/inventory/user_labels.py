"""Human-facing labels; authorization continues to use immutable local user IDs."""
def user_label(user):
    if user is None:
        return 'Unassigned'
    provider = 'Keycloak' if hasattr(user, 'keycloakidentity') else 'LDAP' if hasattr(user, 'ldapidentity') else 'Local'
    name = user.get_full_name().strip()
    if not name:
        name = user.get_username() if provider == 'Local' else f'{provider} user (account #{user.pk})'
    return ' · '.join(part for part in (name, user.email, provider) if part)


def with_identities(users):
    return users.select_related('keycloakidentity', 'ldapidentity')
