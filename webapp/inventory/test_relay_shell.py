"""Navigation scope and preference isolation for the Relay application shell."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Environment, UserPreferences
from .services import prepare_snapshot
from .tests import sample_report


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RelayShellTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('relay-reader')
        self.environment = Environment.objects.create(name='East', slug='east', manager='https://east.example')
        self.client.force_login(self.user)

    def test_viewer_has_module_rail_and_context_without_staff_actions(self):
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'workspace-module-rail')
        self.assertContains(response, 'data-relay-section')
        self.assertContains(response, 'data-primary="collections"')
        self.assertNotContains(response, 'data-primary="administration"')
        self.assertNotContains(response, 'data-primary="work"')
        self.assertContains(response, 'data-module-label="Naming compliance"')

    def test_snapshot_links_keep_historical_snapshot_and_hashes(self):
        snapshot = prepare_snapshot(self.environment, sample_report()); snapshot.save()
        response = self.client.get(reverse('snapshot', args=[snapshot.pk]))
        self.assertContains(response, 'href="#overview" data-primary="inventory"')
        self.assertContains(response, 'href="#dfw-overview" data-primary="firewall"')
        self.assertContains(response, reverse('snapshot', args=[snapshot.pk]) + '#all-groups')
        self.assertContains(response, 'data-section-module="inventory"')

    def test_record_environment_overrides_remembered_environment(self):
        other = Environment.objects.create(name='West', slug='west', manager='https://west.example')
        session = self.client.session; session['selected_environment'] = other.pk; session.save()
        snapshot = prepare_snapshot(self.environment, sample_report()); snapshot.save()
        response = self.client.get(reverse('snapshot', args=[snapshot.pk]))
        self.assertContains(response, f'/naming/?environment={self.environment.pk}')
        self.assertNotContains(response, f'/naming/?environment={other.pk}')
        self.assertContains(response, '<span class="section-environment">East</span>', html=True)

    def test_administration_sections_respect_operator_permissions_and_unsaved_environment(self):
        self.user.is_staff = True; self.user.save()
        response = self.client.get(reverse('administration'))
        self.assertContains(response, 'data-section-module="administration"')
        self.assertContains(response, 'Environments &amp; sync')
        self.assertNotContains(response, 'href="/admin/auth/user/"')
        self.assertEqual(self.client.get(reverse('environment-new')).status_code, 200)

    def test_theme_only_update_preserves_other_preferences_and_is_user_scoped(self):
        UserPreferences.objects.create(user=self.user, theme='system', density='compact', timezone='Europe/Amsterdam', high_contrast=True)
        other = get_user_model().objects.create_user('relay-other')
        response = self.client.post(reverse('theme-preferences'), {'theme': 'dark', 'density': 'comfortable', 'user': other.pk})
        self.assertEqual(response.json(), {'theme': 'dark'})
        saved = UserPreferences.objects.get(user=self.user)
        self.assertEqual((saved.theme, saved.density, saved.timezone, saved.high_contrast), ('dark', 'compact', 'Europe/Amsterdam', True))
        self.assertFalse(UserPreferences.objects.filter(user=other).exists())
        self.assertContains(self.client.get(reverse('dashboard')), 'data-theme="dark"')

    def test_theme_endpoint_validates_post_authentication_and_csrf(self):
        url = reverse('theme-preferences')
        self.assertEqual(self.client.get(url).status_code, 405)
        for value in ('', 'auto', '<script>'):
            self.assertEqual(self.client.post(url, {'theme': value}).status_code, 400)
        csrf = Client(enforce_csrf_checks=True); csrf.force_login(self.user)
        self.assertEqual(csrf.post(url, {'theme': 'light'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(url, {'theme': 'dark'}).status_code, 302)
