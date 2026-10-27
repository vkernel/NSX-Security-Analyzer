from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Environment, AuditEvent
from .credentials import encrypt_password
from .collector import AuditError


@override_settings(STORAGES={'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CredentialTestTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('connection-admin',is_staff=True)
        self.client.force_login(self.user)
        self.env=Environment.objects.create(name='East',slug='east',manager='https://east.example',username='old',password_ciphertext=encrypt_password('saved-secret'))
        self.url=reverse('environment-edit',args=[self.env.pk])
        self.data=dict(name='East',slug='east',manager=self.env.manager,username='new-reader',password='draft-secret',
                       timeout=30,retries=2,sync_interval_minutes=60,max_concurrent_requests=8,enabled='on',action='test_credentials')

    def post(self, **changes):
        return self.client.post(self.url,{**self.data,**changes},HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    @patch('inventory.connection_test.NSXClient')
    def test_draft_test_does_not_save_and_preserves_password_in_browser_only(self, client):
        client.return_value.get.return_value={'results':[]}
        before=Environment.objects.values().get(pk=self.env.pk)
        response=self.post()
        self.assertTrue(response.json()['ok'])
        client.assert_called_once_with(self.env.manager,'new-reader','draft-secret',timeout=10,retries=0,insecure=False,ca_data=None,ca_bundle=None)
        client.return_value.get.assert_called_once_with('/infra/domains',{'page_size':1})
        client.return_value.close.assert_called_once()
        self.assertEqual(before,Environment.objects.values().get(pk=self.env.pk))
        self.assertNotIn('draft-secret',response.content.decode())
        self.assertNotIn('draft-secret',str(list(AuditEvent.objects.values())))
        self.assertNotIn('draft-secret',str(dict(self.client.session)))

    @patch('inventory.connection_test.NSXClient')
    def test_saved_password_requires_same_account_and_origin(self, client):
        client.return_value.get.return_value={'results':[]}
        self.assertFalse(self.post(password='').json()['ok'])
        self.assertFalse(self.post(password='',username='old',manager='https://other.example').json()['ok'])
        client.assert_not_called()
        self.assertTrue(self.post(password='',username='old').json()['ok'])
        self.assertEqual(client.call_args.args[2],'saved-secret')

    @patch('inventory.connection_test.NSXClient')
    def test_errors_are_safe_and_do_not_save(self, client):
        for code in (401,403,500,None):
            client.return_value.get.side_effect=AuditError('secret payload draft-secret',status_code=code)
            response=self.post()
            self.assertFalse(response.json()['ok'])
            self.assertNotIn('draft-secret',response.content.decode())
        self.env.refresh_from_db();self.assertEqual(self.env.username,'old')

    @patch('inventory.connection_test.NSXClient')
    def test_validation_and_permission(self, client):
        self.assertFalse(self.post(timeout=0).json()['ok'])
        client.assert_not_called()
        self.client.force_login(get_user_model().objects.create_user('viewer'))
        self.assertEqual(self.post().status_code,403)
        client.assert_not_called()

    @patch('inventory.connection_test.NSXClient')
    def test_new_environment_and_tls_draft(self, client):
        self.url=reverse('environment-new')
        client.return_value.get.return_value={'results':[]}
        response=self.post(name='West',slug='west',manager='https://west.example',insecure='on')
        self.assertTrue(response.json()['ok'])
        self.assertTrue(client.call_args.kwargs['insecure'])
        self.assertEqual(Environment.objects.count(),1)

    @patch('inventory.connection_test.NSXClient')
    def test_certificate_draft_and_unapproved_preview(self, client):
        self.env.ca_certificate='saved-pem'; self.env.ca_bundle=''; self.env.save()
        client.return_value.get.return_value={'results':[]}
        self.assertTrue(self.post().json()['ok'])
        self.assertEqual(client.call_args.kwargs['ca_data'],'saved-pem')
        self.assertTrue(self.post(remove_ca='on').json()['ok'])
        self.assertIsNone(client.call_args.kwargs['ca_data'])
        client.reset_mock()
        response=self.post(trust_retrieved='on')
        self.assertFalse(response.json()['ok'])
        client.assert_not_called()
        self.env.refresh_from_db(); self.assertEqual(self.env.ca_certificate,'saved-pem')
        self.assertIn('no-store',response.headers['Cache-Control'])
