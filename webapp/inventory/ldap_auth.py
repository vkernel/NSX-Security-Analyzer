"""Explicit LDAPS authentication; no fallback from local credentials."""
import hashlib
import ssl
from contextlib import contextmanager
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db import transaction
from ldap3 import Server, Connection, Tls, NONE, SUBTREE, SIMPLE
from ldap3.utils.conv import escape_filter_chars
from .models import LDAPConfiguration, LDAPIdentity
from .credentials import decrypt_password
from .roles import apply_role
from .audit_events import record


class DirectoryDenied(Exception):
    pass


def current():
    return LDAPConfiguration.objects.filter(pk=1).first() or LDAPConfiguration()


def endpoint(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'ldaps' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or any(c.isspace() for c in value)):
        raise ValueError('Use an LDAPS hostname URL, for example ldaps://directory.example.com:636.')
    return parsed.hostname, parsed.port or 636


class VerifiedTLS(Tls):
    def __init__(self, hostname, pem):
        super().__init__(validate=ssl.CERT_REQUIRED)
        self.hostname = hostname
        self.context = ssl.create_default_context(cadata=pem or None)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        if pem:
            self.context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN

    def wrap_socket(self, connection, do_handshake=False):
        connection.socket = self.context.wrap_socket(connection.socket, server_hostname=self.hostname,
                                                     do_handshake_on_connect=do_handshake)
        return True


@contextmanager
def bound(config, dn, password):
    if not dn or not password:
        raise DirectoryDenied('empty_credentials')
    host, port = endpoint(config.server_url)
    server = Server(host, port=port, use_ssl=True, tls=VerifiedTLS(host, config.ca_certificate),
                    get_info=NONE, connect_timeout=5)
    conn = Connection(server, user=dn, password=password, authentication=SIMPLE,
                      auto_referrals=False, read_only=True, receive_timeout=10, raise_exceptions=True)
    try:
        if not conn.bind():
            raise DirectoryDenied('bind_failed')
        yield conn
    finally:
        conn.unbind()


def lookup(config, username, password):
    with bound(config, config.bind_dn, decrypt_password(config.secret_ciphertext)) as conn:
        conn.search(config.user_base, '(%s=%s)' % (config.username_attribute, escape_filter_chars(username)),
                    search_scope=SUBTREE, attributes=[config.identity_attribute, 'memberOf', 'givenName', 'sn'],
                    size_limit=2, time_limit=10)
        entries = [entry for entry in conn.response if entry.get('type') == 'searchResEntry']
        if conn.result.get('result') != 0 or len(entries) != 1:
            raise DirectoryDenied('user_search_failed')
        entry = entries[0]
    # Validate the user's password before using directory attributes to grant access.
    with bound(config, entry['dn'], password):
        pass
    raw = entry.get('raw_attributes', {})
    subjects = next((v for k, v in raw.items() if k.lower() == config.identity_attribute.lower()), [])
    if len(subjects) != 1 or not subjects[0]:
        raise DirectoryDenied('missing_identity')
    subject = hashlib.sha256(subjects[0]).hexdigest()
    attrs = {k.lower(): v for k, v in entry.get('attributes', {}).items()}
    groups = attrs.get('memberof', [])
    if isinstance(groups, str): groups = [groups]
    groups = {value.casefold() for value in groups if isinstance(value, str)}
    role = next((role for role in ('admin', 'operator', 'viewer')
                 if getattr(config, role + '_group') and getattr(config, role + '_group').casefold() in groups), None)
    if not role:
        raise DirectoryDenied('unmapped_groups')
    def text(key):
        value = attrs.get(key, '')
        if isinstance(value, list): value = value[0] if value else ''
        return value[:150] if isinstance(value, str) else ''
    return subject, role, text('givenname') or username[:150], text('sn')


class LDAPBackend(ModelBackend):
    def authenticate(self, request, ldap_username=None, ldap_password=None, **kwargs):
        if not ldap_username or not ldap_password:
            return None
        config = current()
        if not config.enabled:
            return None
        try:
            subject, role, first, last = lookup(config, ldap_username, ldap_password)
            with transaction.atomic():
                # Serialize provisioning with configuration changes and concurrent first logins.
                saved = LDAPConfiguration.objects.select_for_update().get(pk=1)
                if any(getattr(saved, f.attname) != getattr(config, f.attname) for f in config._meta.fields):
                    raise DirectoryDenied('configuration_changed')
                directory = config.server_url.rstrip('/').lower()
                identity = LDAPIdentity.objects.filter(directory=directory, subject=subject).first()
                if identity:
                    user = get_user_model().objects.select_for_update().get(pk=identity.user_id)
                    if not user.is_active: raise DirectoryDenied('account_disabled')
                else:
                    username = 'ldap_' + hashlib.sha256((directory+'\0'+subject).encode()).hexdigest()
                    user = get_user_model()(username=username)
                    user.set_unusable_password()
                    user.save()
                    LDAPIdentity.objects.create(directory=directory, subject=subject, user=user)
                apply_role(user, role)
                user.first_name, user.last_name = first, last
                user.save()
                record('auth.ldap.provision', 'User', user.pk, actor=user.pk, details={'role': role})
                return user
        except Exception as exc:
            # Never expose directory errors: they can contain DNs, filters or credentials.
            record('auth.ldap.failed', outcome='failed', best_effort=True,
                   details={'reason': str(exc) if isinstance(exc, DirectoryDenied) else 'directory_error',
                            'exception': type(exc).__name__})
            return None
