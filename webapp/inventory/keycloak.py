"""Optional Keycloak OIDC login; tokens never persist in application storage."""
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

from authlib.integrations.django_client import OAuth
from authlib.integrations.django_client.apps import DjangoOAuth2App
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied, ImproperlyConfigured
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache

from .audit_events import record
from .models import KeycloakIdentity
from .roles import apply_role
from . import keycloak_configuration as configuration


DENIAL_REASONS = {
    'disabled': 'Keycloak sign-in is disabled.',
    'invalid_subject': 'The validated ID token has no valid external identity.',
    'missing_roles': 'The ID token has no realm_access.roles claim. Configure the realm-role mapper to include roles in the ID token.',
    'invalid_roles': 'The ID token realm_access.roles claim must be a list of role names.',
    'unmapped_roles': 'No configured application realm role was found in the ID token. Check role assignment, client role scope and the GUI role mappings.',
    'account_disabled': 'The application account is disabled. Contact an application administrator.',
    'expired_state': 'The login attempt expired. Start a fresh sign-in.',
    'missing_state': 'The login session is missing. Start a fresh sign-in using the same browser and application hostname.',
    'changed_configuration': 'Keycloak settings changed during sign-in. Start a fresh sign-in.',
    'invalid_identity': 'The callback has no validated identity or matching nonce. Start a fresh sign-in.',
}


class KeycloakDenied(PermissionDenied):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(DENIAL_REASONS[reason])


class TrustedKeycloakApp(DjangoOAuth2App):
    def _get_session(self):
        # Authlib uses this separate session for JWKS and discovery. Its default
        # implementation does not run compliance_fix like token exchange does.
        session = super()._get_session()
        if self.compliance_fix:
            self.compliance_fix(session)
        return session


class WorkspaceLoginView(LoginView):
    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), 'keycloak_enabled': configuration.current().enabled}


def client(config=None):
    config = config or configuration.current()
    if not config.enabled:
        raise KeycloakDenied('disabled')
    issuer = config.issuer
    parsed = urlsplit(issuer)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not config.client_id
            or not configuration.secret(config)):
        raise ImproperlyConfigured('Keycloak requires an HTTPS realm issuer, client ID and secret.')
    endpoint = issuer + '/protocol/openid-connect'
    return OAuth().register(
        'keycloak', client_cls=TrustedKeycloakApp, compliance_fix=lambda session: configuration.configure_session(session, config), client_id=config.client_id,
        client_secret=configuration.secret(config),
        authorize_url=endpoint + '/auth', access_token_url=endpoint + '/token',
        jwks_uri=endpoint + '/certs', issuer=issuer,
        id_token_signing_alg_values_supported=['RS256'],
        client_kwargs={'scope': 'openid profile email', 'code_challenge_method': 'S256',
                       'token_endpoint_auth_method': 'client_secret_basic',
                       'default_timeout': 15, 'verify': config.ca_bundle or True})


def safe_next(request, value):
    return value if value and url_has_allowed_host_and_scheme(
        value, {request.get_host()}, require_https=request.is_secure()) else reverse(settings.LOGIN_REDIRECT_URL)


@transaction.atomic
def provision(claims, config=None):
    config = config or configuration.current()
    roles_config = {'viewer': config.viewer_role, 'operator': config.operator_role, 'admin': config.admin_role}
    subject = claims.get('sub')
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise KeycloakDenied('invalid_subject')
    realm_access = claims.get('realm_access')
    if realm_access is None:
        raise KeycloakDenied('missing_roles')
    if not isinstance(realm_access, dict):
        raise KeycloakDenied('invalid_roles')
    if 'roles' not in realm_access:
        raise KeycloakDenied('missing_roles')
    roles = realm_access['roles']
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise KeycloakDenied('invalid_roles')
    if not set(roles).intersection(roles_config.values()):
        raise KeycloakDenied('unmapped_roles')
    issuer = config.issuer
    identity = KeycloakIdentity.objects.select_for_update().filter(issuer=issuer, subject=subject).first()
    if identity:
        user = get_user_model().objects.select_for_update().get(pk=identity.user_id)
        if not user.is_active:
            raise KeycloakDenied('account_disabled')
    else:
        # No email/username linking: an IdP must never take over a local administrator.
        username = 'keycloak_' + hashlib.sha256((issuer + '\0' + subject).encode()).hexdigest()
        user = get_user_model()(username=username)
        user.set_unusable_password()
        user.save(force_insert=True)
        KeycloakIdentity.objects.create(issuer=issuer, subject=subject, user=user)
    apply_role(user, 'admin' if roles_config['admin'] in roles
               else 'operator' if roles_config['operator'] in roles else 'viewer')
    user.first_name = str(claims.get('given_name', ''))[:150]
    user.last_name = str(claims.get('family_name', ''))[:150]
    user.email = str(claims.get('email', ''))[:254] if claims.get('email_verified') is True else ''
    user.save()
    record('auth.keycloak.provision', 'User', user.pk, actor=user.pk,
           details={'administrator': user.is_superuser, 'operator': user.is_staff})
    return user


def failed(request, exc, stage='unknown'):
    # Provider exceptions can contain authorization codes, tokens or response bodies.
    reason = exc.reason if isinstance(exc, KeycloakDenied) else 'provider_error'
    record('auth.keycloak.failed', outcome='failed',
           details={'exception': type(exc).__name__, 'reason': reason, 'stage': stage}, best_effort=True)
    explanation = DENIAL_REASONS.get(reason, 'Check the identity provider configuration or contact your administrator.')
    messages.error(request, 'Keycloak sign-in failed. ' + explanation + ' Local sign-in remains available.')
    return redirect('login')


@never_cache
@require_GET
def start(request):
    config = configuration.current()
    if not config.enabled:
        raise PermissionDenied
    try:
        oauth = client(config)
        oauth.framework._clear_session_state(request.session)
        response = oauth.authorize_redirect(request, request.build_absolute_uri(reverse('keycloak_callback')))
        # Keep return destination per authorization attempt, including simultaneous tabs.
        # Authlib stores individual state records; use the state from this redirect.
        state = parse_qs(urlsplit(response.url).query)['state'][0]
        state_key = '_state_keycloak_' + state
        request.session[state_key]['exp'] = time.time() + 600
        request.session[state_key]['data']['configuration'] = configuration.fingerprint(config)
        request.session[state_key]['data']['next'] = safe_next(request, request.GET.get('next'))
        request.session.modified = True
        return response
    except Exception as exc:
        return failed(request, exc, 'authorization_redirect')


@never_cache
@require_GET
def callback(request):
    config = configuration.current()
    if not config.enabled:
        raise PermissionDenied
    stage = 'configuration'
    try:
        oauth = client(config)
        stage = 'session_validation'
        state = request.GET.get('state', '')
        saved = request.session.get('_state_keycloak_' + state, {})
        if not saved:
            raise KeycloakDenied('missing_state')
        if saved.get('exp', 0) < time.time():
            oauth.framework.clear_state_data(request.session, state)
            raise KeycloakDenied('expired_state')
        state_data = oauth.framework.get_state_data(request.session, state)
        if not state_data or not state_data.get('nonce'):
            raise KeycloakDenied('missing_state')
        if state_data.get('configuration') != configuration.fingerprint(config):
            oauth.framework.clear_state_data(request.session, state)
            raise KeycloakDenied('changed_configuration')
        destination = state_data.get('next')
        stage = 'token_validation'
        token = oauth.authorize_access_token(request, claims_options={
            'iss': {'essential': True, 'value': config.issuer}}, leeway=30)
        claims = token.get('userinfo')
        if not claims or claims.get('nonce') != state_data['nonce']:
            raise KeycloakDenied('invalid_identity')
        stage = 'role_provisioning'
        user = provision(claims, config)
        stage = 'session_login'
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        request.session.set_expiry(3600)
        return redirect(safe_next(request, destination))
    except Exception as exc:
        return failed(request, exc, stage)
