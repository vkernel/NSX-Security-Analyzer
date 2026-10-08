import ssl
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.contrib.auth import authenticate, get_user_model
from django.urls import reverse
from .models import LDAPConfiguration, LDAPIdentity
from .credentials import encrypt_password, decrypt_password
from .ldap_auth import LDAPBackend, DirectoryDenied, lookup, bound, VerifiedTLS
from .ldap_settings import ConfigurationForm


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class LDAPTests(TestCase):
    def setUp(self):
        self.config = LDAPConfiguration.objects.create(enabled=True, server_url='ldaps://directory.example:636',
            bind_dn='cn=reader,dc=example', secret_ciphertext=encrypt_password('service-secret'),
            user_base='dc=example', viewer_group='cn=viewers,dc=example', admin_group='cn=admins,dc=example')
        self.admin = get_user_model().objects.create_superuser('local-admin', password='local-secret')

    def data(self, **extra):
        data = {k: getattr(self.config, k) for k in ConfigurationForm.Meta.fields if hasattr(self.config, k)}
        return dict(data, **extra)

    def test_explicit_login_and_identity_isolation(self):
        with patch('inventory.ldap_auth.lookup', return_value=('immutable-id', 'viewer', 'Local', 'Admin')) as lookup_mock:
            self.assertIsNotNone(authenticate(username='local-admin', password='local-secret'))
            self.assertIsNone(authenticate(username='local-admin', password='wrong'))
            lookup_mock.assert_not_called()
            external = authenticate(ldap_username='local-admin', ldap_password='directory-secret')
            self.assertNotEqual(external.pk, self.admin.pk)
            self.assertFalse(external.has_usable_password())
            self.assertFalse(external.is_staff)
            lookup_mock.return_value = ('immutable-id', 'admin', 'New', 'Name')
            same = authenticate(ldap_username='renamed', ldap_password='directory-secret')
            self.assertEqual(same.pk, external.pk)
            self.assertTrue(same.is_superuser)
            lookup_mock.return_value = ('immutable-id', 'viewer', 'New', 'Name')
            self.assertFalse(authenticate(ldap_username='renamed', ldap_password='directory-secret').is_superuser)
            same.is_active = False; same.save()
            self.assertIsNone(authenticate(ldap_username='renamed', ldap_password='directory-secret'))

    def test_disabled_blank_and_failure(self):
        with patch('inventory.ldap_auth.lookup') as call:
            self.assertIsNone(authenticate(ldap_username='a', ldap_password=''))
            self.config.enabled = False; self.config.save()
            self.assertIsNone(authenticate(ldap_username='a', ldap_password='secret'))
            call.assert_not_called()
        self.config.enabled = True; self.config.save()
        with patch('inventory.ldap_auth.lookup', side_effect=DirectoryDenied('unmapped_groups')):
            self.assertIsNone(authenticate(ldap_username='a', ldap_password='secret'))
        self.assertEqual(LDAPIdentity.objects.count(), 0)

    def test_search_escaped_and_groups_after_user_bind(self):
        connection = MagicMock()
        connection.result = {'result': 0}
        entry = {'type': 'searchResEntry', 'dn': 'cn=person,dc=example',
                 'raw_attributes': {'objectGUID': [b'immutable']},
                 'attributes': {'memberOf': ['CN=ADMINS,DC=EXAMPLE'], 'givenName': ['Alex'], 'sn': ['Example']}}
        connection.response = [entry]
        binds = []
        @contextmanager
        def fake_bound(config, dn, password):
            binds.append((dn, password))
            yield connection
        with patch('inventory.ldap_auth.bound', fake_bound):
            subject, role, first, last = lookup(self.config, 'a*)(uid=*)', 'user-secret')
        self.assertEqual((role, first, last), ('admin', 'Alex', 'Example'))
        self.assertEqual(binds[-1], ('cn=person,dc=example', 'user-secret'))
        self.assertIn(r'\2a\29\28', connection.search.call_args.args[1])
        entry['attributes']['memberOf'] = []
        with patch('inventory.ldap_auth.bound', fake_bound), self.assertRaises(DirectoryDenied):
            lookup(self.config, 'a', 'user-secret')
        connection.response = [entry, entry]
        with patch('inventory.ldap_auth.bound', fake_bound), self.assertRaises(DirectoryDenied):
            lookup(self.config, 'a', 'user-secret')

    def test_bind_enforces_tls_and_no_referrals(self):
        with patch('inventory.ldap_auth.Connection') as connection:
            with bound(self.config, 'cn=user', 'secret'):
                pass
            self.assertFalse(connection.call_args.kwargs['auto_referrals'])
            self.assertTrue(connection.call_args.kwargs['read_only'])
            server = connection.call_args.args[0]
            self.assertTrue(server.ssl)
            self.assertEqual(server.tls.context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(server.tls.context.check_hostname)
            connection.return_value.unbind.assert_called_once()
        with self.assertRaises(DirectoryDenied):
            with bound(self.config, 'cn=user', ''): pass

    def test_settings_permissions_validation_and_shared_page(self):
        url = reverse('authentication-settings') + '?provider=ldap'
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), 'Authentication providers')
        self.assertContains(self.client.get(reverse('authentication-settings')), 'Keycloak')
        self.assertFalse(ConfigurationForm(self.data(server_url='ldap://directory.example'), instance=self.config).is_valid())
        self.assertFalse(ConfigurationForm(self.data(username_attribute='uid)(objectClass=*'), instance=self.config).is_valid())
        self.assertFalse(ConfigurationForm(self.data(server_url='ldaps://other.example'), instance=self.config).is_valid())
        reader = get_user_model().objects.create_user('reader')
        self.client.force_login(reader)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_secret_retention_disable_and_certificate_binding(self):
        self.client.force_login(self.admin)
        url = reverse('authentication-settings') + '?provider=ldap'
        data = self.data(enabled=False)
        data.pop('enabled')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.config.refresh_from_db()
        self.assertEqual(decrypt_password(self.config.secret_ciphertext), 'service-secret')
        self.assertNotContains(self.client.get(url), 'service-secret')
        self.assertContains(self.client.post(url, dict(data, trust_retrieved='on')), 'Retrieve a current certificate preview')

    def test_login_safe_redirect_and_generic_failure(self):
        with patch('inventory.ldap_auth.lookup', return_value=('id', 'viewer', 'Alex', 'Example')):
            response = self.client.post(reverse('ldap-login'), {'username': 'a', 'password': 'secret', 'next': 'https://outside.example'})
            self.assertEqual(response.status_code, 302)
            self.assertFalse(response.url.startswith('https://'))
        self.client.logout()
        with patch('inventory.ldap_auth.lookup', side_effect=RuntimeError('sensitive directory response')):
            response = self.client.post(reverse('ldap-login'), {'username': 'a', 'password': 'secret'})
            self.assertContains(response, 'Directory sign-in failed')
            self.assertNotContains(response, 'sensitive directory response')

    def test_real_tls_rejects_unknown_ca_and_wrong_hostname(self):
        import socket
        import tempfile
        import threading
        from pathlib import Path
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from ldap3 import Server, Connection, NONE
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
                .not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), False)
                .sign(key, hashes.SHA256()))
        pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        with tempfile.TemporaryDirectory() as directory:
            certfile, keyfile = Path(directory)/'cert.pem', Path(directory)/'key.pem'
            certfile.write_text(pem)
            keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(certfile, keyfile)
            listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(); listener.settimeout(10)
            port = listener.getsockname()[1]
            def serve():
                for _ in range(3):
                    raw, _ = listener.accept()
                    try:
                        with context.wrap_socket(raw, server_side=True): pass
                    except ssl.SSLError: raw.close()
            thread = threading.Thread(target=serve, daemon=True); thread.start()
            try:
                for host, trust, success in [('localhost', '', False), ('localhost', pem, True), ('127.0.0.1', pem, False)]:
                    conn = Connection(Server(host, port=port, use_ssl=True, get_info=NONE, connect_timeout=2,
                                             tls=VerifiedTLS(host, trust)), auto_referrals=False, raise_exceptions=True)
                    try:
                        if success:
                            conn.open(read_server_info=False)
                            self.assertFalse(conn.closed)
                        else:
                            with self.assertRaises(Exception): conn.open(read_server_info=False)
                    finally:
                        if conn.socket: conn.socket.close()
            finally:
                thread.join(timeout=10); listener.close()

    def test_certificate_retrieval_preserves_form_but_requires_explicit_trust(self):
        self.client.force_login(self.admin)
        url = reverse('authentication-settings') + '?provider=ldap'
        with patch('inventory.ldap_settings.retrieve_manager', return_value={'pem': 'preview', 'certificates': []}):
            response = self.client.post(url, self.data(action='retrieve', bind_password='typed-password'))
        self.assertEqual(response.context['form']['bind_password'].value(), 'typed-password')
        self.config.refresh_from_db()
        self.assertEqual(self.config.ca_certificate, '')
        self.assertNotIn('typed-password', str(dict(self.client.session)))

    def test_secondary_failover_keeps_identity_and_uses_own_trust(self):
        from ldap3.core.exceptions import LDAPSocketOpenError
        self.config.secondary_server_url = 'ldaps://replica.example:636'
        self.config.ca_certificate = 'primary-trust'
        self.config.secondary_ca_certificate = 'secondary-trust'
        self.config.save()
        result = ('stable-id', 'viewer', 'Alex', 'Example')
        with patch('inventory.ldap_auth.lookup_server', return_value=result):
            primary_user = authenticate(ldap_username='alex', ldap_password='user-secret')
        with patch('inventory.ldap_auth.lookup_server', side_effect=[LDAPSocketOpenError('unavailable'), result]) as call:
            secondary_user = authenticate(ldap_username='alex', ldap_password='user-secret')
            fallback_config = call.call_args_list[1].args[0]
            self.assertEqual(fallback_config.server_url, self.config.secondary_server_url)
            self.assertEqual(fallback_config.ca_certificate, 'secondary-trust')
        self.assertEqual(primary_user.pk, secondary_user.pk)
        self.assertEqual(LDAPIdentity.objects.count(), 1)
        self.config.refresh_from_db()
        self.assertEqual(self.config.server_url, 'ldaps://directory.example:636')

    def test_no_failover_on_wrong_password_or_missing_groups(self):
        from ldap3.core.exceptions import LDAPInvalidCredentialsResult
        self.config.secondary_server_url = 'ldaps://replica.example:636'
        for error in (LDAPInvalidCredentialsResult(), DirectoryDenied('unmapped_groups'), DirectoryDenied('user_search_failed')):
            with patch('inventory.ldap_auth.lookup_server', side_effect=error) as call:
                with self.assertRaises(type(error)):
                    lookup(self.config, 'alex', 'wrong')
                self.assertEqual(call.call_count, 1)

    def test_single_server_and_both_unavailable(self):
        with patch('inventory.ldap_auth.lookup_server', side_effect=OSError('unavailable')) as call:
            with self.assertRaises(OSError): lookup(self.config, 'alex', 'secret')
            self.assertEqual(call.call_count, 1)
        self.config.secondary_server_url = 'ldaps://replica.example:636'
        with patch('inventory.ldap_auth.lookup_server', side_effect=OSError('unavailable')) as call:
            with self.assertRaises(OSError): lookup(self.config, 'alex', 'secret')
            self.assertEqual(call.call_count, 2)

    def test_two_certificate_previews_are_independent_and_both_tested(self):
        self.client.force_login(self.admin)
        url = reverse('authentication-settings') + '?provider=ldap'
        data = self.data(secondary_server_url='ldaps://replica.example:636', bind_password='new-secret')
        with patch('inventory.ldap_settings.retrieve_manager', side_effect=[{'pem': 'primary-cert', 'certificates': []}, {'pem': 'secondary-cert', 'certificates': []}]):
            self.client.post(url, dict(data, action='retrieve'))
            self.client.post(url, dict(data, action='retrieve_secondary'))
        session = self.client.session
        self.assertEqual(session['ldap_certificate_preview']['pem'], 'primary-cert')
        self.assertEqual(session['ldap_secondary_certificate_preview']['pem'], 'secondary-cert')
        connection = MagicMock(); connection.result = {'result': 0}; connection.entries = [object()]
        targets = []
        @contextmanager
        def fake_bound(config, dn, password):
            targets.append((config.server_url, config.ca_certificate))
            yield connection
        with patch('inventory.ldap_auth.bound', fake_bound):
            response = self.client.post(url, dict(data, action='save', trust_retrieved='on', trust_secondary='on'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(targets, [('ldaps://directory.example:636', 'primary-cert'), ('ldaps://replica.example:636', 'secondary-cert')])
        self.config.refresh_from_db()
        self.assertEqual(self.config.secondary_ca_certificate, 'secondary-cert')
        self.assertNotIn('ldap_secondary_certificate_preview', self.client.session)

    def test_secondary_validation_and_url_changes_clear_only_its_trust(self):
        self.assertFalse(ConfigurationForm(self.data(secondary_server_url=self.config.server_url, bind_password='secret'), instance=self.config).is_valid())
        self.assertFalse(ConfigurationForm(self.data(secondary_server_url='ldap://replica.example'), instance=self.config).is_valid())
        self.assertFalse(ConfigurationForm(self.data(secondary_server_url='ldaps://replica.example'), instance=self.config).is_valid())
        self.config.refresh_from_db()
        self.config.secondary_server_url = 'ldaps://replica.example'
        self.config.secondary_ca_certificate = 'old-secondary'
        self.config.ca_certificate = 'primary'
        self.config.save()
        self.client.force_login(self.admin)
        data = self.data(secondary_server_url='', action='save')
        data.pop('enabled')
        response = self.client.post(reverse('authentication-settings')+'?provider=ldap', data)
        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertEqual(self.config.secondary_ca_certificate, '')
        self.assertEqual(self.config.ca_certificate, 'primary')
