"""LDAPS configuration and explicit directory sign-in."""
import re
import time
from django import forms
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from . import ldap_auth
from .models import LDAPConfiguration
from .credentials import encrypt_password
from .certificates import retrieve_manager
from .audit_events import record
from .keycloak import safe_next


class ConfigurationForm(forms.ModelForm):
    bind_password = forms.CharField(required=False, strip=False, widget=forms.PasswordInput(render_value=True),
        help_text='Leave blank to retain the encrypted service-account password.')
    trust_retrieved = forms.BooleanField(required=False, label='I verified the fingerprint and trust this certificate')
    trust_secondary = forms.BooleanField(required=False, label='I verified the secondary fingerprint and trust this certificate')
    remove_secondary_certificate = forms.BooleanField(required=False, label='Use system trust for the secondary server')
    remove_certificate = forms.BooleanField(required=False, label='Use system certificate trust')

    class Meta:
        model = LDAPConfiguration
        fields = ['enabled', 'server_url', 'secondary_server_url', 'bind_dn', 'bind_password', 'user_base', 'username_attribute',
                  'identity_attribute', 'viewer_group', 'operator_group', 'admin_group', 'remove_certificate', 'trust_retrieved', 'remove_secondary_certificate', 'trust_secondary']
        labels = {'server_url': 'Primary server URL', 'secondary_server_url': 'Secondary server URL (optional)', 'enabled': 'Enable LDAPS sign-in', 'bind_dn': 'Service-account bind DN', 'user_base': 'User search base DN',
                  'viewer_group': 'Viewer group DN', 'operator_group': 'Operator group DN', 'admin_group': 'Administrator group DN'}
        help_texts = {'secondary_server_url': 'Optional replica of the SAME directory. Both servers use the same service account, user base and group mappings.', 'identity_attribute': 'Immutable identity attribute: objectGUID for Active Directory, entryUUID for OpenLDAP.',
                      'username_attribute': 'sAMAccountName for Active Directory, uid for OpenLDAP. Group mapping uses direct memberOf values.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_server = self.instance.server_url
        self.original_secondary = self.instance.secondary_server_url
        self.has_secret = bool(self.instance.secret_ciphertext)

    def clean_server_url(self):
        value = self.cleaned_data['server_url'].strip().rstrip('/')
        if value:
            try: ldap_auth.endpoint(value)
            except ValueError as exc: raise forms.ValidationError(str(exc))
        return value

    def clean_secondary_server_url(self):
        value = self.cleaned_data['secondary_server_url'].strip().rstrip('/')
        if value:
            try: ldap_auth.endpoint(value)
            except ValueError as exc: raise forms.ValidationError(str(exc))
        return value

    def clean(self):
        data = super().clean()
        for field in ('username_attribute', 'identity_attribute'):
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*', data.get(field, '')):
                self.add_error(field, 'Enter an LDAP attribute name.')
        if data.get('enabled'):
            for field in ('server_url', 'bind_dn', 'user_base'):
                if not data.get(field): self.add_error(field, 'Required when LDAPS is enabled.')
            if not data.get('bind_password') and (not self.has_secret or data.get('server_url') != self.original_server):
                self.add_error('bind_password', 'Enter the service-account password for this server.')
            if data.get('secondary_server_url') and data.get('secondary_server_url') != self.original_secondary and not data.get('bind_password'):
                self.add_error('bind_password', 'Re-enter the service-account password when adding or changing the secondary server.')
            groups = [data.get(role + '_group', '').casefold() for role in ('viewer', 'operator', 'admin')]
            groups = [v for v in groups if v]
            if not groups: self.add_error(None, 'Configure at least one group DN.')
            if len(groups) != len(set(groups)): self.add_error(None, 'Use different groups for each application role.')
        if data.get('trust_retrieved') and data.get('remove_certificate'):
            self.add_error(None, 'Choose retrieved trust or system trust.')
        if data.get('server_url') and data.get('secondary_server_url') and ldap_auth.endpoint(data['server_url']) == ldap_auth.endpoint(data['secondary_server_url']):
            self.add_error('secondary_server_url', 'Choose a different server, or leave this blank for a single server.')
        if data.get('trust_secondary') and data.get('remove_secondary_certificate'):
            self.add_error(None, 'Choose retrieved or system trust for the secondary server.')
        if data.get('trust_secondary') and not data.get('secondary_server_url'):
            self.add_error('secondary_server_url', 'Enter a secondary server before approving its certificate.')
        return data


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_page(request):
    if not request.user.is_superuser: return HttpResponseForbidden('Administrator access is required.')
    config = ldap_auth.current()
    old_server = config.server_url
    old_secondary = config.secondary_server_url
    preview = request.session.get('ldap_certificate_preview')
    if preview and time.time() - preview.get('created', 0) > 600:
        request.session.pop('ldap_certificate_preview', None)
        preview = None
    secondary_preview = request.session.get('ldap_secondary_certificate_preview')
    if secondary_preview and time.time() - secondary_preview.get('created', 0) > 600:
        request.session.pop('ldap_secondary_certificate_preview', None)
        secondary_preview = None
    form = ConfigurationForm(request.POST or None, instance=config)
    action = request.POST.get('action')
    if request.method == 'POST' and action in ('retrieve', 'retrieve_secondary'):
        secondary = action == 'retrieve_secondary'
        session_key = 'ldap_secondary_certificate_preview' if secondary else 'ldap_certificate_preview'
        request.session.pop(session_key, None)
        if secondary: secondary_preview = None
        else: preview = None
        try:
            server = request.POST.get('secondary_server_url' if secondary else 'server_url', '').strip().rstrip('/')
            host, port = ldap_auth.endpoint(server)
            host = '['+host+']' if ':' in host else host
            retrieved = dict(retrieve_manager('https://%s:%s' % (host, port)), server=server, created=time.time())
            request.session[session_key] = retrieved
            if secondary: secondary_preview = retrieved
            else: preview = retrieved
            record('ldap.certificate_retrieved', 'LDAPConfiguration', 1, details={'server': 'secondary' if secondary else 'primary'})
        except Exception as exc:
            record('ldap.certificate_retrieval_failed', 'LDAPConfiguration', 1, outcome='failed', details={'exception': type(exc).__name__})
            messages.error(request, 'Certificate retrieval failed. Check the selected LDAPS hostname, port, certificate validity and connectivity.')
    elif request.method == 'POST' and form.is_valid():
        updated = form.save(commit=False)
        if updated.server_url != old_server:
            updated.ca_certificate = updated.secret_ciphertext = ''
        if updated.secondary_server_url != old_secondary or not updated.secondary_server_url:
            updated.secondary_ca_certificate = ''
        if form.cleaned_data['bind_password']:
            updated.secret_ciphertext = encrypt_password(form.cleaned_data['bind_password'])
        if form.cleaned_data['remove_certificate']: updated.ca_certificate = ''
        if form.cleaned_data['trust_retrieved']:
            if not preview or preview['server'] != updated.server_url:
                form.add_error(None, 'Retrieve a current certificate preview for this server first.')
            else: updated.ca_certificate = preview['pem']
        if form.cleaned_data['remove_secondary_certificate']: updated.secondary_ca_certificate = ''
        if form.cleaned_data['trust_secondary']:
            if not secondary_preview or secondary_preview['server'] != updated.secondary_server_url:
                form.add_error(None, 'Retrieve a current certificate preview for the secondary server first.')
            else: updated.secondary_ca_certificate = secondary_preview['pem']
        if not form.errors:
            try:
                if updated.enabled or action == 'test' or form.cleaned_data['trust_retrieved'] or form.cleaned_data['trust_secondary']:
                    from .credentials import decrypt_password
                    from ldap3 import BASE
                    for server_role, server_config in ldap_auth.servers(updated):
                        try:
                            with ldap_auth.bound(server_config, updated.bind_dn, decrypt_password(updated.secret_ciphertext)) as conn:
                                conn.search(updated.user_base, '(objectClass=*)', search_scope=BASE, attributes=['objectClass'], size_limit=1, time_limit=10)
                                if conn.result.get('result') != 0 or not conn.entries:
                                    raise ValueError('search_base')
                        except Exception:
                            form.add_error(None, '%s server verification failed.' % server_role.capitalize())
                            raise
                if action == 'test':
                    messages.success(request, 'TLS, service-account bind and search base verified for every configured server. Settings not saved; test a user sign-in to verify groups.')
                    record('ldap.connection_test', 'LDAPConfiguration', 1)
                else:
                    updated.save()
                    request.session.pop('ldap_certificate_preview', None)
                    request.session.pop('ldap_secondary_certificate_preview', None)
                    messages.success(request, 'LDAPS settings saved. Local sign-in remains available.')
                    from django.urls import reverse
                    return redirect(reverse('authentication-settings') + '?provider=ldap')
            except Exception as exc:
                form.add_error(None, 'LDAPS test failed. Check certificate trust, hostname, bind credentials, search base and network access.')
                record('ldap.connection_test', 'LDAPConfiguration', 1, outcome='failed', details={'exception': type(exc).__name__})
    return render(request, 'inventory/ldap_settings.html', {'form': form, 'preview': preview, 'secondary_preview': secondary_preview})


class SignInForm(forms.Form):
    username = forms.CharField(max_length=255)
    password = forms.CharField(strip=False, widget=forms.PasswordInput)


@never_cache
@require_http_methods(['GET', 'POST'])
def sign_in(request):
    if not ldap_auth.current().enabled: return redirect('login')
    form = SignInForm(request.POST or None)
    target = safe_next(request, request.POST.get('next') or request.GET.get('next'))
    if request.method == 'POST' and form.is_valid():
        user = authenticate(request, ldap_username=form.cleaned_data['username'], ldap_password=form.cleaned_data['password'])
        if user:
            login(request, user)
            request.session.set_expiry(3600)
            return redirect(target)
        form.add_error(None, 'Directory sign-in failed. Check credentials and application group membership, or contact your administrator.')
    return render(request, 'registration/ldap_login.html', {'form': form, 'next': target})
