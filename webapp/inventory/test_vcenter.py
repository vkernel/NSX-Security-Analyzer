"""Synthetic vCenter discovery and mapping regression tests."""
import time
import ssl
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from django.test import SimpleTestCase, TestCase, override_settings
from django.contrib.auth import get_user_model
from .ipfix.vcenter import management_addresses, origin, discover, DiscoveryError
from .models import Environment, IPFIXExporter


class DiscoveryTests(SimpleTestCase):
    def test_selected_management_interfaces_only(self):
        def nic(key, address):
            return NS(key=key, device=key, spec=NS(ip=NS(ipAddress=address)))
        config = NS(selectedVnic=['management', 'invalid'], candidateVnic=[
            nic('management', '192.0.2.10'), nic('vmotion', '192.0.2.20'), nic('invalid', '127.0.0.1')])
        self.assertEqual(management_addresses(config), [('192.0.2.10', 'management')])

    def test_origin_validation(self):
        self.assertEqual(origin('vcenter.example.invalid'), ('vcenter.example.invalid', 443))
        for value in ['http://host', 'https://user:pass@host', 'https://host/path', 'https://host:bad']:
            with self.subTest(value=value), self.assertRaises(DiscoveryError):
                origin(value)

    @patch('inventory.ipfix.vcenter.Disconnect')
    @patch('inventory.ipfix.vcenter.SmartConnect')
    def test_inventory_read_and_cleanup(self, connect, disconnect):
        nic = NS(key='key1', device='vmk0', spec=NS(ip=NS(ipAddress='192.0.2.10')))
        manager = Mock()
        manager.QueryNetConfig.return_value = NS(selectedVnic=['key1'], candidateVnic=[nic])
        host = NS(_moId='host-1', name='esxi.example.invalid', parent=NS(_moId='domain-1', name='Cluster'),
                  configManager=NS(virtualNicManager=manager))
        view = Mock(view=[host])
        content = connect.return_value.RetrieveContent.return_value
        content.about.apiType = 'VirtualCenter'
        content.viewManager.CreateContainerView.return_value = view
        rows, issues = discover('vc.example.invalid', 'reader', 'synthetic-password')
        self.assertEqual(rows[0]['address'], '192.0.2.10')
        self.assertEqual(issues, [])
        manager.QueryNetConfig.assert_called_once_with('management')
        view.Destroy.assert_called_once()
        disconnect.assert_called_once_with(connect.return_value)

    @patch('inventory.ipfix.vcenter.SmartConnect', side_effect=RuntimeError('synthetic-secret'))
    def test_errors_do_not_echo_credentials(self, connect):
        with self.assertRaises(DiscoveryError) as error:
            discover('vc.example.invalid', 'reader', 'synthetic-secret')
        self.assertNotIn('synthetic-secret', str(error.exception))

    @patch('inventory.ipfix.vcenter.SmartConnect')
    def test_certificate_errors_explain_the_actual_failure(self, connect):
        for code, expected in [(62, 'does not match'), (10, 'expired'), (20, 'issuing root CA')]:
            error = ssl.SSLCertVerificationError('untrusted diagnostic text')
            error.verify_code = code
            connect.side_effect = error
            with self.subTest(code=code), self.assertRaises(DiscoveryError) as caught:
                discover('vc.example.invalid', 'reader', 'synthetic-secret')
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn('untrusted diagnostic text', str(caught.exception))

    @patch('inventory.ipfix.vcenter.SmartConnect', side_effect=RuntimeError())
    def test_uploaded_ca_is_used_with_hostname_verification(self, connect):
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic test CA')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
        pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
        with self.assertRaises(DiscoveryError):
            discover('vc.example.invalid', 'reader', 'synthetic-secret', pem)
        context = connect.call_args.kwargs['sslContext']
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIn(certificate.public_bytes(serialization.Encoding.DER), context.get_ca_certs(binary_form=True))


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DiscoveryViewTests(TestCase):
    url = '/administration/ipfix/vcenter/'

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='reader', is_staff=True)
        self.client.force_login(self.user)
        self.environment = Environment.objects.create(slug='example', name='Example', manager='nsx.example.invalid')

    def preview(self, created=None):
        session = self.client.session
        session['ipfix_discovery'] = {'created': created or time.time(), 'environment': self.environment.pk,
            'environment_name': 'Example', 'issues': [], 'rows': [
                {'host': 'one', 'cluster': 'Cluster', 'device': 'vmk0', 'address': '192.0.2.1'},
                {'host': 'two', 'cluster': 'Cluster', 'device': 'vmk0', 'address': '192.0.2.2'}]}
        session.save()

    @patch('inventory.ipfix.vcenter.discover', return_value=([], []))
    def test_credentials_not_persisted_or_rendered(self, discovery):
        response = self.client.post(self.url, {'action': 'discover', 'environment': self.environment.pk,
            'server': 'vc.example.invalid', 'username': 'reader', 'password': 'synthetic-secret'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'synthetic-secret')
        self.assertNotIn('synthetic-secret', str(dict(self.client.session)))
        discovery.assert_called_once()

    def test_only_selected_addresses_added(self):
        self.preview()
        self.assertEqual(self.client.post(self.url, {'action': 'apply', 'selected': ['1']}).status_code, 302)
        self.assertEqual(list(IPFIXExporter.objects.values_list('address', flat=True)), ['192.0.2.2'])

    def test_cross_environment_conflict_rolls_back_entire_selection(self):
        other = Environment.objects.create(slug='other', name='Other', manager='other.example.invalid')
        IPFIXExporter.objects.create(environment=other, address='192.0.2.2')
        self.preview()
        response = self.client.post(self.url, {'action': 'apply', 'selected': ['0', '1']})
        self.assertContains(response, 'belongs to another environment')
        self.assertEqual(IPFIXExporter.objects.count(), 1)

    def test_expired_and_tampered_previews_rejected(self):
        self.preview(time.time() - 601)
        self.assertContains(self.client.post(self.url, {'action': 'apply', 'selected': ['0']}), 'expired')
        self.preview()
        self.assertContains(self.client.post(self.url, {'action': 'apply', 'selected': ['99']}), 'Select hosts')
        self.assertFalse(IPFIXExporter.objects.exists())

    def test_staff_required(self):
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)

    @patch('inventory.ipfix.vcenter.discover', return_value=([], []))
    def test_retrieved_ca_requires_confirmation_and_matching_origin(self, discovery):
        session = self.client.session
        session['vcenter_ca_preview'] = {'created': time.time(), 'server': 'vc.example.invalid',
            'origin': ['vc.example.invalid', 443], 'pem': 'synthetic-pem', 'certificates': []}
        session.save()
        data = {'action': 'discover', 'environment': self.environment.pk, 'server': 'other.example.invalid',
            'username': 'reader', 'password': 'synthetic-secret', 'trust_retrieved': 'on'}
        self.assertContains(self.client.post(self.url, data), 'another vCenter')
        discovery.assert_not_called()
        data['server'] = 'vc.example.invalid'
        self.client.post(self.url, data)
        self.assertEqual(discovery.call_args.args[3], 'synthetic-pem')
        self.assertNotIn('vcenter_ca_preview', self.client.session)

    @patch('inventory.ipfix.vcenter.discover', return_value=([], []))
    def test_retrieved_ca_not_used_without_confirmation(self, discovery):
        session = self.client.session
        session['vcenter_ca_preview'] = {'created': time.time(), 'server': 'vc.example.invalid',
            'origin': ['vc.example.invalid', 443], 'pem': 'synthetic-pem', 'certificates': []}
        session.save()
        self.client.post(self.url, {'action': 'discover', 'environment': self.environment.pk,
            'server': 'vc.example.invalid', 'username': 'reader', 'password': 'synthetic-secret'})
        self.assertIsNone(discovery.call_args.args[3])
