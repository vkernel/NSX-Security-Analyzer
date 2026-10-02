from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import KeycloakIdentity


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class LocalRoleTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('role-admin', password='test-password')
        self.client.force_login(self.admin)

    def test_create_each_role(self):
        for role, staff, superuser in [('viewer', False, False), ('operator', True, False), ('admin', True, True)]:
            response = self.client.post(reverse('admin:auth_user_add'), {
                'username': 'role-test-' + role, 'usable_password': 'true', 'password1': 'Test-only-access-729!',
                'password2': 'Test-only-access-729!', 'role': role, '_save': 'Save'})
            self.assertEqual(response.status_code, 302)
            user = get_user_model().objects.get(username='role-test-' + role)
            self.assertEqual((user.is_staff, user.is_superuser), (staff, superuser))
            self.assertTrue(user.check_password('Test-only-access-729!'))

    def edit(self, user, role):
        return self.client.post(reverse('admin:auth_user_change', args=[user.pk]), {
            'username': user.username, 'role': role, 'is_active': 'on',
            'date_joined_0': '2026-10-02', 'date_joined_1': '10:00:00', '_save': 'Save'})

    def test_role_downgrade_and_existing_role_display(self):
        user = get_user_model().objects.create_superuser('second-admin')
        response = self.client.get(reverse('admin:auth_user_change', args=[user.pk]))
        self.assertEqual(response.context['adminform'].form.fields['role'].initial, 'admin')
        self.assertEqual(self.edit(user, 'viewer').status_code, 302)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_operator_cannot_manage_roles(self):
        user = get_user_model().objects.create_user('operator', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('admin:auth_user_add')).status_code, 403)
        self.assertEqual(self.edit(user, 'admin').status_code, 403)
        user.refresh_from_db()
        self.assertFalse(user.is_superuser)

    def test_external_role_is_read_only(self):
        user = get_user_model().objects.create_user('external', is_staff=True)
        KeycloakIdentity.objects.create(user=user, issuer='https://id.example/realm', subject='external')
        self.assertEqual(self.edit(user, 'admin').status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
