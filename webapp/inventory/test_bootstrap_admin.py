from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase

from .models import AuditEvent

provision = import_module('inventory.migrations.0017_initial_administrator').provision_administrator


class InitialAdministratorTests(TestCase):
    def setUp(self):
        User.objects.all().delete()
        AuditEvent.objects.all().delete()

    def provision(self):
        provision(apps, SimpleNamespace(connection=connection))

    def test_initial_account_and_repeat_preserves_password(self):
        self.provision()
        user = User.objects.get(username='admin')
        self.assertTrue(user.check_password('NSXSecurityA!'))
        self.assertNotEqual(user.password, 'NSXSecurityA!')
        self.assertTrue(user.is_active and user.is_staff and user.is_superuser)
        self.assertEqual(AuditEvent.objects.filter(action='administrator.provisioned').count(), 1)
        user.set_password('Changed-password-for-test')
        user.save()
        self.provision()
        user.refresh_from_db()
        self.assertTrue(user.check_password('Changed-password-for-test'))
        self.assertEqual(AuditEvent.objects.filter(action='administrator.provisioned').count(), 1)

    def test_existing_unprivileged_disabled_admin_is_not_promoted(self):
        user = User.objects.create_user(username='Admin', password='Existing-test-password', is_active=False)
        self.provision()
        user.refresh_from_db()
        self.assertFalse(user.is_active or user.is_staff or user.is_superuser)
        self.assertTrue(user.check_password('Existing-test-password'))
        self.assertEqual(User.objects.count(), 1)

    def test_existing_superuser_keeps_installation_unchanged(self):
        User.objects.create_superuser('operator', password='Existing-test-password')
        self.provision()
        self.assertFalse(User.objects.filter(username='admin').exists())
