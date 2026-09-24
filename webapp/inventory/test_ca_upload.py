from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from .forms import EnvironmentForm
from .models import Environment
from .services import enqueue, claim_job, execute_job, engine
from .tests import sample_report


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CAUploadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Test CA')])
        cls.pem = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc)-timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc)+timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))
        cls.operator = get_user_model().objects.create_user('ca-admin', is_staff=True)

    def data(self, **changes):
        return dict(name='East', slug='east', manager='east.example', username='reader', password='test-password',
            workers=4, timeout=30, retries=2, enabled=True, sync_interval_minutes=60, **changes)

    def upload(self, content=None):
        return SimpleUploadedFile('company-ca.pem', self.pem if content is None else content)

    def test_upload_persists_and_blank_edit_keeps_certificate(self):
        self.client.force_login(self.operator)
        response = self.client.post(reverse('environment-new'), {**self.data(), 'ca_upload': self.upload()})
        self.assertEqual(response.status_code, 302)
        environment = Environment.objects.get()
        self.assertEqual(environment.ca_certificate, self.pem.decode())
        self.assertEqual(environment.ca_filename, 'company-ca.pem')
        response = self.client.get(reverse('environment-edit', args=[environment.pk]))
        self.assertContains(response, 'multipart/form-data')
        self.assertContains(response, 'type="file"')
        self.assertContains(response, 'company-ca.pem')
        self.assertNotContains(response, 'name="ca_bundle"')
        form = EnvironmentForm(self.data(), instance=environment)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().ca_certificate, self.pem.decode())
        remove = EnvironmentForm(self.data(remove_ca=True), instance=environment)
        self.assertTrue(remove.is_valid(), remove.errors)
        self.assertEqual(remove.save().ca_certificate, '')

    def test_invalid_oversized_and_private_key_files_rejected(self):
        for content in [b'not a certificate', b'x'*(1024*1024+1), self.pem+b'-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----', b'\xff']:
            form = EnvironmentForm(self.data(), {'ca_upload': self.upload(content)})
            self.assertFalse(form.is_valid())
            self.assertIn('ca_upload', form.errors)
        form = EnvironmentForm(self.data(remove_ca=True), {'ca_upload': self.upload()})
        self.assertFalse(form.is_valid())
        self.assertIn('remove_ca', form.errors)

    def test_replacement_clears_legacy_path_and_bundle_is_supported(self):
        environment = Environment.objects.create(name='East', slug='east', manager='https://east.example', ca_bundle='/legacy/ca.pem')
        form = EnvironmentForm(self.data(), {'ca_upload': self.upload(self.pem+self.pem)}, instance=environment)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().ca_bundle, '')

    def test_worker_uses_frozen_uploaded_ca_without_a_file_path(self):
        form = EnvironmentForm(self.data(), {'ca_upload': self.upload()})
        self.assertTrue(form.is_valid(), form.errors)
        environment = form.save()
        job = enqueue(environment, self.operator)
        Environment.objects.filter(pk=environment.pk).update(ca_certificate='changed after enqueue')
        claim_job()
        audit = engine()
        real_client = audit.NSXClient
        with patch.object(audit, 'NSXClient', wraps=real_client) as client, patch.object(audit, 'audit', return_value=sample_report()):
            execute_job(job.pk)
        self.assertEqual(client.call_args.kwargs['ca_data'], self.pem.decode())
        self.assertIsNone(client.call_args.kwargs['ca_bundle'])
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded')

    def test_viewer_cannot_upload(self):
        viewer = get_user_model().objects.create_user('ca-viewer')
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(reverse('environment-new'), {**self.data(), 'ca_upload': self.upload()}).status_code, 403)
        self.assertFalse(Environment.objects.exists())
