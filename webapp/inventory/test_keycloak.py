import time
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from joserfc import jwt
from joserfc.jwk import RSAKey
from .keycloak import client, provision
from .models import KeycloakIdentity


@override_settings(KEYCLOAK_ENABLED=True, KEYCLOAK_ISSUER='https://id.example/realms/test',
                   KEYCLOAK_CLIENT_ID='analyzer', KEYCLOAK_CLIENT_SECRET='test-secret',
                   STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class KeycloakTests(TestCase):
    def claims(self, **extra):
        return {'sub': 'external-user', 'realm_access': {'roles': ['nsx-analyzer-viewer']}, **extra}

    def test_roles_are_updated_and_local_accounts_not_linked(self):
        local = get_user_model().objects.create_user('local', email='same@example.com')
        user = provision(self.claims(email='same@example.com', email_verified=True))
        self.assertNotEqual(user.pk, local.pk)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        user = provision(self.claims(realm_access={'roles': ['nsx-analyzer-admin']}))
        self.assertTrue(user.is_superuser)
        user = provision(self.claims())
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertEqual(KeycloakIdentity.objects.count(), 1)
        user.is_active = False
        user.save()
        with self.assertRaises(PermissionDenied):
            provision(self.claims())

    def test_profile_display_names_preserve_identity(self):
        user = provision(self.claims(given_name='Alex', family_name='Example'))
        internal_name = user.username
        self.assertEqual(user.get_full_name(), 'Alex Example')
        user = provision(self.claims(name='Alex Display', preferred_username='alex'))
        self.assertEqual(user.get_full_name(), 'Alex Display')
        user = provision(self.claims(given_name=None, family_name=[], preferred_username='alex'))
        self.assertEqual(user.get_full_name(), 'alex')
        self.assertEqual(user.username, internal_name)
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertContains(response, '<span class="user-name">alex</span>', html=True)
        self.assertNotContains(response, internal_name)
        user = provision(self.claims())
        self.assertEqual(user.get_full_name(), 'Keycloak user')

    def test_unassigned_role_cannot_create_user(self):
        with self.assertRaises(PermissionDenied):
            provision(self.claims(realm_access={'roles': ['unrelated']}))
        self.assertFalse(KeycloakIdentity.objects.exists())

    def test_disabled_and_invalid_state(self):
        with override_settings(KEYCLOAK_ENABLED=False):
            self.assertEqual(self.client.get('/login/keycloak/').status_code, 403)
        result = self.client.get('/login/keycloak/callback/?code=private-code&state=invalid')
        self.assertRedirects(result, '/login/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_authorization_uses_pkce_and_nonce(self):
        response = self.client.get('/login/keycloak/?next=https://evil.example/')
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.url).query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertTrue(query['nonce'])
        self.assertTrue(query['state'])
        state = query['state'][0]
        self.assertFalse(self.client.session['_state_keycloak_' + state]['data']['next'].startswith('https://evil'))

    def test_valid_callback_and_replay_rejected(self):
        response = self.client.get('/login/keycloak/?next=/environments/')
        query = parse_qs(urlsplit(response.url).query)
        claims = self.claims(nonce=query['nonce'][0])
        key = RSAKey.generate_key(2048)
        token = self.signed_token(key, claims)
        with patch('authlib.integrations.django_client.apps.DjangoOAuth2App.fetch_access_token', return_value={'id_token': token}), patch('authlib.integrations.base_client.sync_openid.OpenIDMixin.fetch_jwk_set', return_value={'keys': [key.as_dict(private=False)]}):
            url = '/login/keycloak/callback/?code=test&state=' + query['state'][0]
            self.assertEqual(self.client.get(url).status_code, 302)
            self.assertIn('_auth_user_id', self.client.session)
            self.assertEqual(self.client.get(url)['Location'], '/login/')

    def signed_token(self, key, extra=None):
        now = int(time.time())
        claims = {'iss': 'https://id.example/realms/test', 'sub': 'external-user',
                  'aud': 'analyzer', 'iat': now, 'exp': now + 300, 'nonce': 'expected'}
        claims.update(extra or {})
        return jwt.encode({'alg': 'RS256'}, claims, key)

    def test_token_validation_rejects_wrong_claims_and_signature(self):
        key = RSAKey.generate_key(2048)
        oauth = client()
        with patch.object(oauth, 'fetch_jwk_set', return_value={'keys': [key.as_dict(private=False)]}):
            for extra in ({'iss': 'https://evil.example'}, {'aud': 'other-client'},
                          {'exp': int(time.time())-1000}, {'nonce': 'wrong'}):
                with self.subTest(extra=extra), self.assertRaises(Exception):
                    oauth.parse_id_token({'id_token': self.signed_token(key, extra)}, nonce='expected',
                                         claims_options={'iss': {'value': 'https://id.example/realms/test'}})
            with self.assertRaises(Exception):
                oauth.parse_id_token({'id_token': self.signed_token(RSAKey.generate_key(2048))}, nonce='expected')

    def test_expired_state_does_not_exchange_code(self):
        response = self.client.get('/login/keycloak/')
        state = parse_qs(urlsplit(response.url).query)['state'][0]
        session = self.client.session
        session['_state_keycloak_' + state]['exp'] = time.time() - 1
        session.save()
        with patch('authlib.integrations.django_client.apps.DjangoOAuth2App.fetch_access_token') as exchange:
            self.client.get('/login/keycloak/callback/?code=test&state=' + state)
            exchange.assert_not_called()
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_permission_denials_have_safe_specific_reasons(self):
        from .keycloak import KeycloakDenied, failed
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        cases = [({}, 'missing_roles'), ({'realm_access': None}, 'missing_roles'),
                 ({'realm_access': []}, 'invalid_roles'),
                 ({'realm_access': {'roles': 'admin'}}, 'invalid_roles'),
                 ({'realm_access': {'roles': []}}, 'unmapped_roles')]
        for extra, reason in cases:
            with self.subTest(reason=reason):
                claims = {'sub': 'user', **extra}
                with self.assertRaises(KeycloakDenied) as caught:
                    provision(claims)
                self.assertEqual(caught.exception.reason, reason)
        request = RequestFactory().get('/login/')
        request.session = {}
        request._messages = FallbackStorage(request)
        with patch('inventory.keycloak.record') as audit:
            failed(request, KeycloakDenied('missing_roles'), 'role_provisioning')
        self.assertEqual(audit.call_args.kwargs['details']['reason'], 'missing_roles')
        self.assertEqual(audit.call_args.kwargs['details']['stage'], 'role_provisioning')
        with patch('inventory.keycloak.record') as audit:
            failed(request, ValueError('private-token-value'), 'token_validation')
        self.assertNotIn('private-token-value', str(audit.call_args))
        self.assertNotIn('private-token-value', str(list(request._messages)))

    def test_client_roles_are_scoped_and_do_not_merge_realm_roles(self):
        from .keycloak import KeycloakDenied
        from .models import KeycloakConfiguration
        config = KeycloakConfiguration(issuer='https://id.example/realms/test', client_id='analyzer', role_source='client')
        claims = {'sub': 'client-user', 'realm_access': {'roles': ['nsx-analyzer-admin']},
                  'resource_access': {'other-client': {'roles': ['nsx-analyzer-admin']},
                                      'analyzer': {'roles': ['nsx-analyzer-viewer']}}}
        user = provision(claims, config)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        claims['resource_access']['analyzer']['roles'] = ['nsx-analyzer-operator', 'nsx-analyzer-admin']
        self.assertTrue(provision(claims, config).is_superuser)
        claims['resource_access']['analyzer']['roles'] = ['nsx-analyzer-viewer']
        self.assertFalse(provision(claims, config).is_superuser)
        del claims['resource_access']['analyzer']
        with self.assertRaises(KeycloakDenied) as exc:
            provision(claims, config)
        self.assertEqual(exc.exception.reason, 'missing_client_roles')
        for invalid in ([], {'analyzer': []}, {'analyzer': {'roles': 'nsx-analyzer-admin'}}):
            with self.subTest(invalid=invalid), self.assertRaises(KeycloakDenied):
                provision(dict(claims, resource_access=invalid), config)
        config.role_source = 'realm'
        with self.assertRaises(KeycloakDenied):
            provision({'sub': 'client-only', 'resource_access': {'analyzer': {'roles': ['nsx-analyzer-admin']}}}, config)

    def test_role_source_changes_invalidate_pending_login(self):
        from .keycloak_configuration import fingerprint
        from .models import KeycloakConfiguration
        config = KeycloakConfiguration(client_id='analyzer')
        previous = fingerprint(config)
        config.role_source = 'client'
        self.assertNotEqual(fingerprint(config), previous)
