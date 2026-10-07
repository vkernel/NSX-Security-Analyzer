from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import KeycloakConfiguration
from .credentials import decrypt_password
from .keycloak_configuration import current


@override_settings(KEYCLOAK_ENABLED=False, KEYCLOAK_CLIENT_SECRET='', STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class KeycloakSettingsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('integration-admin', is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)
        self.url = reverse('keycloak-settings')
        self.data = {'enabled': 'on', 'issuer': 'https://identity.example/realms/test', 'client_id': 'analyzer',
            'client_secret': 'private-test-secret', 'viewer_role': 'viewer', 'operator_role': 'operator', 'admin_role': 'admin', 'action': 'save'}

    def test_admin_permissions_save_encryption_and_secret_preservation(self):
        with patch('inventory.keycloak_settings.test_connection', return_value='Verified'):
            response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 302)
        stored = KeycloakConfiguration.objects.get()
        self.assertNotEqual(stored.secret_ciphertext, self.data['client_secret'])
        self.assertEqual(decrypt_password(stored.secret_ciphertext), self.data['client_secret'])
        self.assertTrue(current().enabled)
        self.assertNotContains(self.client.get(self.url), self.data['client_secret'])
        with patch('inventory.keycloak_settings.test_connection', return_value='Verified'):
            self.client.post(self.url, dict(self.data, client_secret=''))
        stored.refresh_from_db()
        self.assertEqual(decrypt_password(stored.secret_ciphertext), self.data['client_secret'])
        self.admin.is_superuser = False; self.admin.save()
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)

    def test_disabling_overrides_environment_and_local_login_survives(self):
        with override_settings(KEYCLOAK_ENABLED=True):
            data = dict(self.data); data.pop('enabled')
            self.client.post(self.url, data)
            self.assertFalse(current().enabled)
            self.client.logout()
            self.assertEqual(self.client.get('/login/').status_code, 200)
            self.assertEqual(self.client.get('/login/keycloak/').status_code, 403)

    def test_retrieve_preserves_draft_but_requires_explicit_matching_trust(self):
        preview = {'pem': 'test-certificate', 'certificates': [{'fingerprint': 'AA:BB'}]}
        with patch('inventory.keycloak_settings.retrieve_manager', return_value=preview):
            response = self.client.post(self.url, dict(self.data, action='retrieve'))
        self.assertContains(response, 'AA:BB')
        self.assertContains(response, 'private-test-secret')  # only the submitted draft, masked
        self.assertNotIn('private-test-secret', str(dict(self.client.session)))
        self.assertFalse(KeycloakConfiguration.objects.exists())
        with patch('inventory.keycloak_settings.test_connection', return_value='Verified'):
            self.client.post(self.url, dict(self.data, trust_retrieved='on', issuer='https://other.example/realms/test'))
        self.assertFalse(KeycloakConfiguration.objects.exists())
        with patch('inventory.keycloak_settings.test_connection', return_value='Verified'):
            self.client.post(self.url, dict(self.data, trust_retrieved='on'))
        self.assertEqual(KeycloakConfiguration.objects.get().ca_certificate, 'test-certificate')

    def test_failed_tls_does_not_save_or_reveal_exception(self):
        import requests
        with patch('inventory.keycloak_settings.test_connection', side_effect=requests.exceptions.SSLError('private-provider-response')):
            response = self.client.post(self.url, self.data)
        self.assertContains(response, 'TLS verification failed')
        self.assertNotContains(response, 'private-provider-response')
        self.assertFalse(KeycloakConfiguration.objects.exists())

    def test_changed_issuer_requires_new_secret_and_clears_old_trust(self):
        with patch('inventory.keycloak_settings.test_connection', return_value='Verified'):
            self.client.post(self.url, self.data)
            response = self.client.post(self.url, dict(self.data, issuer='https://other.example/realms/test', client_secret=''))
        self.assertContains(response, 'saved credentials are not reused across issuers')
        self.assertEqual(KeycloakConfiguration.objects.get().issuer, self.data['issuer'])

    def test_connection_test_does_not_save(self):
        with patch('inventory.keycloak_settings.test_connection', return_value='Verified'):
            response = self.client.post(self.url, dict(self.data, action='test'))
        self.assertContains(response, 'Settings have not been saved')
        self.assertFalse(KeycloakConfiguration.objects.exists())

    def test_pending_login_invalidated_when_configuration_changes(self):
        from urllib.parse import parse_qs, urlsplit
        with patch('inventory.keycloak_settings.test_connection', return_value='Verified'):
            self.client.post(self.url, self.data)
        self.client.logout()
        response = self.client.get('/login/keycloak/')
        state = parse_qs(urlsplit(response.url).query)['state'][0]
        KeycloakConfiguration.objects.update(client_id='replacement')
        with patch('authlib.integrations.django_client.apps.DjangoOAuth2App.fetch_access_token') as exchange:
            response = self.client.get('/login/keycloak/callback/', {'state': state, 'code': 'private-code'})
        exchange.assert_not_called()
        self.assertEqual(response.url, '/login/')

    def test_certificate_adapter_verifies_real_tls_and_hostname(self):
        import ssl
        import tempfile
        import threading
        import json
        from pathlib import Path
        from datetime import datetime, timedelta, timezone
        from http.server import HTTPServer, BaseHTTPRequestHandler
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from .keycloak_settings import test_connection
        import requests
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
            .not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
            .sign(key, hashes.SHA256()))
        pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                issuer = 'https://localhost:%s/realms/test' % self.server.server_port
                base = issuer + '/protocol/openid-connect'
                body = json.dumps(dict(issuer=issuer, authorization_endpoint=base+'/auth', token_endpoint=base+'/token', jwks_uri=base+'/certs')).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args): pass
        with tempfile.TemporaryDirectory() as directory:
            certfile, keyfile = Path(directory)/'cert.pem', Path(directory)/'key.pem'
            certfile.write_text(pem)
            keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            server = HTTPServer(('127.0.0.1', 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(certfile, keyfile)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                config = KeycloakConfiguration(issuer='https://localhost:%s/realms/test' % server.server_port)
                with self.assertRaises(requests.exceptions.SSLError): test_connection(config)
                config.ca_certificate = pem
                self.assertIn('verified', test_connection(config))
                config.issuer = config.issuer.replace('localhost', '127.0.0.1')
                with self.assertRaises(requests.exceptions.SSLError): test_connection(config)
            finally:
                server.shutdown(); server.server_close(); thread.join()
