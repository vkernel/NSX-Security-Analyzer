"""Get started checklist, notifications page and sign-in card."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AuditJob, Environment, UserPreferences


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AccountPageTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_superuser('account-admin', password='synthetic-only')
        self.viewer = users.objects.create_user('account-viewer')
        self.client.force_login(self.admin)

    def test_get_started_reflects_the_workspace(self):
        response = self.client.get(reverse('getting-started'))
        states = [step['state'] for step in response.context['steps']]
        self.assertEqual(states, ['current', 'next', 'next', 'next', 'next'])
        self.assertContains(response, reverse('environment-new'))
        env = Environment.objects.create(name='Start', slug='start', manager='https://start.invalid')
        AuditJob.objects.create(environment=env, status='succeeded', finished_at=timezone.now())
        response = self.client.get(reverse('getting-started'))
        self.assertEqual([step['state'] for step in response.context['steps']][:3], ['done', 'done', 'current'])
        self.assertEqual(response.context['complete'], 2)
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('getting-started'))
        self.assertNotContains(response, reverse('environment-new'))
        self.assertContains(response, 'Ask an administrator')

    def test_notifications_page_lists_the_feed_and_marks_read(self):
        env = Environment.objects.create(name='Feed', slug='feed', manager='https://feed.invalid')
        AuditJob.objects.create(environment=env, status='failed', finished_at=timezone.now())
        response = self.client.get(reverse('notifications-page'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unread'], 1)
        self.assertContains(response, 'Collection failed')
        self.assertContains(response, 'is-unread')
        response = self.client.post(reverse('notifications-read'))
        self.assertRedirects(response, reverse('notifications-page'))
        self.assertIsNotNone(UserPreferences.objects.get(user=self.admin).notifications_seen_at)
        self.assertEqual(self.client.get(reverse('notifications-page')).context['unread'], 0)
        # The header menu keeps its JSON contract.
        self.assertEqual(self.client.post(reverse('notifications-read'), data='{}', content_type='application/json').json(), {'saved': True})
        self.assertEqual(set(self.client.get(reverse('notifications')).json()), {'items', 'unread'})

    def test_menus_link_the_account_pages_and_sign_in_is_one_card(self):
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, reverse('getting-started'))
        self.assertContains(response, reverse('notifications-page'))
        self.client.logout()
        response = self.client.get(reverse('login'))
        self.assertContains(response, 'Read-only analysis of saved NSX configuration.')
        self.assertEqual(self.client.get(reverse('getting-started')).status_code, 302)
