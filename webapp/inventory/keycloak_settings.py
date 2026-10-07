"""Administrator-only Keycloak configuration and credential-free trust review."""
import json
import time
from urllib.parse import urlsplit
import requests
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from . import keycloak_configuration as configuration
from .audit_events import record
from .certificates import retrieve_manager, DiscoveryError
from .credentials import encrypt_password
from .models import KeycloakConfiguration


def issuer_url(value):
    value = value.strip().rstrip('/')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not parsed.path.strip('/')
                or any(c.isspace() for c in value) or len(value) > 512):
            raise ValueError()
    except ValueError:
        raise forms.ValidationError('Enter the full HTTPS realm issuer, for example https://identity.example.com/realms/security.')
    return value


class ConfigurationForm(forms.ModelForm):
    client_secret = forms.CharField(required=False, strip=False, widget=forms.PasswordInput(render_value=True),
        help_text='Leave blank to keep the saved secret. Saved secrets are never displayed.')
    remove_certificate = forms.BooleanField(required=False, label='Use the default certificate trust store',
        help_text='Removes saved certificate trust, including an old CA file setting.')
    trust_retrieved = forms.BooleanField(required=False, label='I verified the fingerprint and trust this certificate')

    class Meta:
        model = KeycloakConfiguration
        fields = ['enabled', 'issuer', 'client_id', 'client_secret', 'role_source', 'viewer_role', 'operator_role', 'admin_role',
                  'remove_certificate', 'trust_retrieved']
        labels = {'enabled': 'Enable Keycloak sign-in', 'issuer': 'Realm issuer URL', 'client_id': 'Client ID',
                  'viewer_role': 'Viewer role', 'operator_role': 'Operator role', 'admin_role': 'Administrator role'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role_source'].help_text = 'Client roles are read only from resource_access[Client ID].roles in the ID token. Realm roles use realm_access.roles.'
        self.previous_issuer = self.instance.issuer
        self.had_secret = bool(self.instance.secret_ciphertext) or (self.instance._state.adding and bool(configuration.settings.KEYCLOAK_CLIENT_SECRET))

    def clean_issuer(self):
        value = self.cleaned_data.get('issuer', '')
        return issuer_url(value) if value else ''

    def clean(self):
        values = super().clean()
        if values.get('issuer') != self.previous_issuer and self.previous_issuer and not values.get('client_secret') and values.get('enabled'):
            self.add_error('client_secret', 'Enter the client secret for the new issuer; saved credentials are not reused across issuers.')
        if values.get('enabled'):
            for field in ('issuer', 'client_id'):
                if not values.get(field): self.add_error(field, 'Required when Keycloak is enabled.')
            if not values.get('client_secret') and not self.had_secret:
                self.add_error('client_secret', 'Enter the Keycloak client secret.')
        roles = [values.get(k) for k in ('viewer_role', 'operator_role', 'admin_role')]
        if len(set(roles)) != 3: self.add_error(None, 'Use a distinct role for each application role.')
        if values.get('remove_certificate') and values.get('trust_retrieved'):
            self.add_error(None, 'Choose either retrieved certificate trust or the default trust store.')
        return values


def test_connection(config):
    issuer = issuer_url(config.issuer)
    with configuration.configure_session(requests.Session(), config) as session:
        with session.get(issuer + '/.well-known/openid-configuration', timeout=(5, 10), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError('Realm discovery returned HTTP %s. Check the issuer URL and Gateway route.' % response.status_code)
            raw = bytearray()
            for chunk in response.iter_content(8192):
                raw.extend(chunk)
                if len(raw) > 1024 * 1024: raise ValueError('Realm discovery response is too large.')
            try:
                metadata = json.loads(raw)
            except (ValueError, TypeError):
                raise ValueError('Realm discovery did not return valid JSON.') from None
            base = issuer + '/protocol/openid-connect'
            expected = {'issuer': issuer, 'authorization_endpoint': base+'/auth', 'token_endpoint': base+'/token', 'jwks_uri': base+'/certs'}
            if not isinstance(metadata, dict) or any(metadata.get(k) != v for k, v in expected.items()):
                raise ValueError('Realm discovery endpoints do not match the configured issuer. Check the public Keycloak hostname and realm.')
    return 'TLS and realm discovery verified. Complete a Keycloak sign-in to validate the client secret, redirect URI and roles.'


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_page(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden('Administrator access is required.')
    config = configuration.current()
    legacy = config._state.adding
    original_issuer = config.issuer
    original_ciphertext = config.secret_ciphertext
    preview = request.session.get('keycloak_certificate_preview')
    if preview and time.time() - preview.get('created', 0) > 600:
        request.session.pop('keycloak_certificate_preview', None)
        preview = None
    form = ConfigurationForm(request.POST or None, instance=config)
    action = request.POST.get('action')
    if request.method == 'POST' and action == 'retrieve':
        request.session.pop('keycloak_certificate_preview', None)
        preview = None
        try:
            issuer = issuer_url(request.POST.get('issuer', ''))
            parsed = urlsplit(issuer)
            preview = dict(retrieve_manager('https://' + parsed.netloc), issuer=issuer, created=time.time())
            request.session['keycloak_certificate_preview'] = preview
            record('keycloak.certificate_retrieved', 'KeycloakConfiguration', 1)
        except (forms.ValidationError, DiscoveryError):
            messages.error(request, 'Could not retrieve the certificate. Check the HTTPS realm issuer, certificate validity and connectivity.')
            record('keycloak.certificate_retrieval_failed', 'KeycloakConfiguration', 1, outcome='failed')
    elif request.method == 'POST' and form.is_valid():
        updated = form.save(commit=False)
        new_secret = form.cleaned_data.get('client_secret')
        if updated.issuer != original_issuer:
            updated.ca_certificate = updated.ca_bundle = ''
            updated.secret_ciphertext = ''
        if new_secret:
            updated.secret_ciphertext = encrypt_password(new_secret)
        elif legacy and updated.issuer == original_issuer and configuration.settings.KEYCLOAK_CLIENT_SECRET:
            updated.secret_ciphertext = encrypt_password(configuration.settings.KEYCLOAK_CLIENT_SECRET)
        if form.cleaned_data['remove_certificate']:
            updated.ca_certificate = updated.ca_bundle = ''
        if form.cleaned_data['trust_retrieved']:
            if not preview or preview['issuer'] != updated.issuer:
                form.add_error(None, 'Certificate preview expired or belongs to another issuer. Retrieve it again.')
            else:
                updated.ca_certificate, updated.ca_bundle = preview['pem'], ''
        if not form.errors:
            try:
                if action == 'test' or updated.enabled or form.cleaned_data['trust_retrieved']:
                    result = test_connection(updated)
                else:
                    result = 'Keycloak sign-in disabled. Local sign-in remains available.'
                if action == 'test':
                    messages.success(request, result + ' Settings have not been saved.')
                    record('keycloak.connection_test', 'KeycloakConfiguration', 1)
                else:
                    updated.save()
                    request.session.pop('keycloak_certificate_preview', None)
                    messages.success(request, 'Keycloak settings saved. ' + result)
                    return redirect('keycloak-settings')
            except requests.exceptions.SSLError:
                form.add_error(None, 'TLS verification failed. Retrieve and review the certificate, and check the issuer hostname and certificate validity.')
                record('keycloak.connection_test', 'KeycloakConfiguration', 1, outcome='failed', details={'exception': 'SSLError'})
            except (requests.RequestException, OSError):
                form.add_error(None, 'Connection failed. Check DNS, network connectivity and certificate file access from the web pod.')
                record('keycloak.connection_test', 'KeycloakConfiguration', 1, outcome='failed')
            except ValueError as exc:
                form.add_error(None, str(exc))
                record('keycloak.connection_test', 'KeycloakConfiguration', 1, outcome='failed')
    return render(request, 'inventory/keycloak_settings.html', {'form': form, 'preview': preview,
        'legacy': legacy, 'has_secret': bool(original_ciphertext) or (legacy and bool(configuration.settings.KEYCLOAK_CLIENT_SECRET)),
        'has_certificate': bool(config.ca_certificate or config.ca_bundle),
        'callback_url': request.build_absolute_uri(reverse('keycloak_callback'))})
