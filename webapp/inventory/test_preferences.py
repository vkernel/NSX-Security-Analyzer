from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import UserPreferences, Environment, Snapshot, AuditJob
from .tests import sample_report


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PreferenceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('preferences-viewer')
        self.other = get_user_model().objects.create_user('other-viewer')
        self.environment = Environment.objects.create(name='East', slug='east', manager='https://east.example')
        self.client.force_login(self.user)

    def test_defaults_do_not_write_and_login_required(self):
        response = self.client.get(reverse('website-settings'))
        self.assertContains(response, 'Appearance')
        self.assertEqual(UserPreferences.objects.count(), 0)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('website-settings')).status_code, 302)

    def test_save_only_changes_own_preferences(self):
        data = {'page_size': 10, 'report_page_size': 100, 'density': 'compact', 'history_days': 90, 'refresh_seconds': 30, 'user': self.other.pk, 'timezone':'UTC', 'date_format':'readable', 'theme':'system', 'text_size':'normal', 'landing_page':'overview'}
        response = self.client.post(reverse('website-settings'), data)
        self.assertRedirects(response, reverse('website-settings'))
        saved = UserPreferences.objects.get(user=self.user)
        self.assertEqual(saved.page_size, 10)
        self.assertFalse(UserPreferences.objects.filter(user=self.other).exists())
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'display-compact')
        self.assertContains(response, 'data-refresh-seconds="30"')
        self.assertContains(response, 'data-report-page-size="100"')
        self.client.force_login(self.other)
        self.assertContains(self.client.get(reverse('dashboard')), 'display-comfortable')

    def test_invalid_settings_do_not_save(self):
        response = self.client.post(reverse('website-settings'), {'page_size': 100000, 'density': 'script', 'report_page_size': 0, 'history_days': 1, 'refresh_seconds': 0})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserPreferences.objects.exists())

    def test_snapshot_and_collection_history_paginate(self):
        stamp = timezone.now()
        Snapshot.objects.bulk_create([Snapshot(environment=self.environment, generated_at=stamp, report=sample_report()) for _ in range(23)])
        AuditJob.objects.bulk_create([AuditJob(environment=self.environment, status='failed') for _ in range(23)])
        UserPreferences.objects.create(user=self.user, page_size=10, history_days=7)
        response = self.client.get(reverse('environment', args=[self.environment.pk]), {'page': 2})
        self.assertEqual(len(response.context['snapshots']), 10)
        self.assertEqual(response.context['snapshots'].paginator.num_pages, 3)
        response = self.client.get(reverse('collection-history', args=[self.environment.pk]), {'page': 3})
        self.assertEqual(len(response.context['jobs']), 3)
        self.assertContains(response, 'Page 3 of 3')
        response = self.client.get(reverse('rule-history', args=[self.environment.pk]))
        self.assertEqual(response.context['days'], 7)
        self.assertEqual(Snapshot.objects.count(), 23)
