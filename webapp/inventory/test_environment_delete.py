from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from .demo import create_demo_environment
from .models import AuditJob, Environment, Snapshot, UserPreferences


@override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class EnvironmentDeletionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('operator', is_staff=True)
        self.client.force_login(self.user)
        self.environment = create_demo_environment()
        self.url = reverse('environment-delete', args=[self.environment.pk])

    def test_confirmed_deletion_removes_only_selected_history(self):
        other = create_demo_environment()
        job = AuditJob.objects.create(environment=self.environment, status='succeeded')
        snapshot = self.environment.snapshots.get()
        snapshot.job = job
        snapshot.save()
        UserPreferences.objects.create(user=self.user, preferred_environment=self.environment)
        session = self.client.session
        session['selected_environment'] = self.environment.pk
        session.save()
        self.assertContains(self.client.get(self.url), '1 snapshot')
        self.assertTrue(Environment.objects.filter(pk=self.environment.pk).exists())
        self.assertEqual(self.client.post(self.url, {'confirmation': 'wrong'}).status_code, 400)
        response = self.client.post(self.url, {'confirmation': self.environment.slug})
        self.assertRedirects(response, reverse('environment-directory'))
        self.assertFalse(Environment.objects.filter(pk=self.environment.pk).exists())
        self.assertFalse(AuditJob.objects.filter(pk=job.pk).exists())
        self.assertFalse(Snapshot.objects.filter(pk=snapshot.pk).exists())
        self.assertTrue(other.snapshots.exists())
        self.assertIsNone(UserPreferences.objects.get(user=self.user).preferred_environment_id)
        self.assertNotIn('selected_environment', self.client.session)

    def test_active_collections_block_deletion(self):
        for status in ('queued', 'running'):
            job = AuditJob.objects.create(environment=self.environment, status=status)
            self.assertEqual(self.client.post(self.url, {'confirmation': self.environment.slug}).status_code, 400)
            self.assertTrue(self.environment.snapshots.exists())
            job.delete()

    def test_authorization_csrf_and_methods(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, {'confirmation': self.environment.slug}).status_code, 403)
        self.assertEqual(self.client.delete(self.url).status_code, 405)
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'confirmation': self.environment.slug}).status_code, 403)
        self.assertTrue(Environment.objects.filter(pk=self.environment.pk).exists())

    def test_demo_action_is_part_of_adding_an_environment(self):
        self.assertNotContains(self.client.get(reverse('environment-directory')), 'Add testing data')
        self.assertContains(self.client.get(reverse('environment-new')), 'Create demo environment')
        self.assertContains(self.client.get(reverse('environment-edit', args=[self.environment.pk])), 'Delete environment')
