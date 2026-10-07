"""Database configuration with legacy environment fallback and verified TLS."""
import hashlib
import json
import ssl
from django.conf import settings
from requests.adapters import HTTPAdapter
from .models import KeycloakConfiguration
from .credentials import decrypt_password


def current():
    stored = KeycloakConfiguration.objects.filter(pk=1).first()
    if stored:
        return stored
    return KeycloakConfiguration(enabled=settings.KEYCLOAK_ENABLED, issuer=settings.KEYCLOAK_ISSUER,
        client_id=settings.KEYCLOAK_CLIENT_ID, ca_bundle=settings.KEYCLOAK_CA_BUNDLE,
        viewer_role=settings.KEYCLOAK_ROLES['viewer'], operator_role=settings.KEYCLOAK_ROLES['operator'],
        admin_role=settings.KEYCLOAK_ROLES['admin'])


def secret(config):
    return decrypt_password(config.secret_ciphertext) if config.secret_ciphertext else (settings.KEYCLOAK_CLIENT_SECRET if config._state.adding else '')


def fingerprint(config):
    # In-flight authorizations cannot complete against changed credentials or trust.
    data = [config.enabled, config.issuer, config.client_id, config.secret_ciphertext,
            config.ca_certificate, config.ca_bundle, config.role_source, config.viewer_role, config.operator_role, config.admin_role]
    if config._state.adding: data.append(settings.KEYCLOAK_CLIENT_SECRET)
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()


class CertificateAdapter(HTTPAdapter):
    def __init__(self, pem):
        self.context = ssl.create_default_context(cadata=pem)
        self.context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        super().__init__()

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host, kwargs = super().build_connection_pool_key_attributes(request, verify, cert)
        kwargs['ssl_context'] = self.context
        kwargs['cert_reqs'] = 'CERT_REQUIRED'
        return host, kwargs


def configure_session(session, config):
    if config.ca_certificate:
        session.mount('https://', CertificateAdapter(config.ca_certificate))
    session.verify = config.ca_bundle or True
    return session
