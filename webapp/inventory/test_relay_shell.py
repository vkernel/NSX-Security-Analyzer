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
        self.assertContains(response, 'data-primary="work"')
        self.assertContains(response, 'data-module-label="Reviews"')
        self.assertContains(response, 'data-module-label="Naming compliance"')

    def test_snapshot_links_keep_historical_snapshot_and_hashes(self):
        snapshot = prepare_snapshot(self.environment, sample_report()); snapshot.save()
        response = self.client.get(reverse('snapshot', args=[snapshot.pk]))
        self.assertContains(response, 'href="#overview" data-primary="inventory"')
        self.assertContains(response, 'href="#dfw-overview" data-primary="firewall"')
        self.assertContains(response, reverse('snapshot', args=[snapshot.pk]) + '#all-groups')
        self.assertContains(response, 'data-section-module="inventory"')

    def test_section_menu_preference_is_restored_without_changing_other_user(self):
        key = 'menu:workspace:section-panel'
        response = self.client.post(reverse('interface-preferences'),
                                    {'key': key, 'value': False}, content_type='application/json')
        self.assertEqual(response.json(), {'saved': True})
        page = self.client.get(reverse('dashboard'))
        self.assertContains(page, 'data-section-expanded="false"')
        self.assertContains(page, 'aria-controls="primary-sidebar" aria-expanded="false"')
        self.assertContains(page, 'aria-label="Expand navigation"')
        self.assertContains(page, 'data-nav-shell')
        other = get_user_model().objects.create_user('navigation-other')
        self.client.force_login(other)
        self.assertContains(self.client.get(reverse('dashboard')), 'data-section-expanded="true"')

    def test_disabled_menu_memory_ignores_stored_collapsed_state(self):
        UserPreferences.objects.create(user=self.user, remember_menus=False,
                                       interface_state={'menu:workspace:section-panel': False})
        self.assertContains(self.client.get(reverse('dashboard')), 'data-section-expanded="true"')

    def test_primary_navigation_uses_full_module_names(self):
        self.user.is_staff = True; self.user.save()
        response = self.client.get(reverse('dashboard'))
        for label in ('Environments', 'Naming compliance', 'Segmentation', 'Collections', 'Administration'):
            self.assertContains(response, f'<span>{label}</span>', html=True)
        for old in ('Environs', 'Segments', 'Collect', 'Admin'):
            self.assertNotContains(response, f'<span>{old}</span>', html=True)

    def test_record_environment_overrides_remembered_environment(self):
        other = Environment.objects.create(name='West', slug='west', manager='https://west.example')
        session = self.client.session; session['selected_environment'] = other.pk; session.save()
        snapshot = prepare_snapshot(self.environment, sample_report()); snapshot.save()
        response = self.client.get(reverse('snapshot', args=[snapshot.pk]))
        self.assertContains(response, f'/naming/?environment={self.environment.pk}')
        self.assertNotContains(response, f'/naming/?environment={other.pk}')
        self.assertContains(response, f'<a href="/environments/{self.environment.pk}/">East</a>', html=True)

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


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class GroupedNavigationTests(TestCase):
    """Grouped sidebar, collapsed rail and the saved-snapshot search palette."""

    def setUp(self):
        self.environment = Environment.objects.create(name='East', slug='east', manager='https://east.example')

    def login(self, **flags):
        user = get_user_model().objects.create_user('nav-' + '-'.join(sorted(flags)) if flags else 'nav-viewer', **flags)
        self.client.force_login(user)
        return user

    def test_every_module_lists_its_pages_with_permission_filtering(self):
        self.login()
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'data-nav-module="inventory"')
        self.assertContains(response, 'id="nav-pages-work"')
        self.assertContains(response, '<p class="nav-section-label">Explore</p>', html=True)
        self.assertContains(response, '<p class="nav-section-label">Work</p>', html=True)
        self.assertContains(response, 'data-primary="activity"')
        self.assertContains(response, '?queue=all')
        # Viewers do not get owner or second-approval queues, or administration.
        self.assertNotContains(response, '?queue=assigned')
        self.assertNotContains(response, '?queue=second')
        self.assertNotContains(response, 'id="nav-pages-administration"')
        # Only the current module starts expanded.
        self.assertContains(response, 'id="nav-pages-inventory" data-nav-pages role="group" aria-label="Inventory pages" hidden')

    def test_operator_queues_and_administrator_pages(self):
        self.login(is_staff=True, is_superuser=True)
        response = self.client.get(reverse('my-work') + '?queue=assigned')
        self.assertContains(response, '?queue=assigned" aria-current="page"')
        self.assertContains(response, '?queue=second')
        self.assertContains(response, 'data-primary="work" data-module-label="Reviews" title="Reviews" aria-label="Reviews" aria-current="true"')
        admin = self.client.get(reverse('administration'))
        self.assertContains(admin, 'id="nav-pages-administration"')
        self.assertContains(admin, '<p class="nav-pages-heading">Access</p>', html=True)
        self.assertContains(admin, reverse('review-approvals'))

    def test_activity_is_its_own_module(self):
        self.login()
        response = self.client.get(reverse('activity'))
        self.assertContains(response, 'data-primary="activity" data-module-label="Activity" title="Activity" aria-label="Activity" aria-current="page"')

    def test_search_index_lists_pages_and_environments_without_inventory(self):
        self.login()
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'id="navigation-index"')
        self.assertContains(response, 'Reviews \\u203a All findings')
        self.assertContains(response, '"type": "Environments", "label": "East"')
        self.assertContains(response, 'data-search-url="' + reverse('navigation-search') + '"')

    def test_search_endpoint_uses_saved_snapshot_records_only(self):
        from unittest.mock import patch
        from .services import engine
        from .snapshot_index import build
        self.login()
        report = sample_report()
        report['objects'][0]['name'] = 'web-tier-prod'
        snapshot = prepare_snapshot(self.environment, report); snapshot.save()
        build(snapshot)
        with patch.object(engine(), 'NSXClient', side_effect=AssertionError('Search contacted NSX')):
            data = self.client.get(reverse('navigation-search'), {'q': 'WEB-T'}).json()
        self.assertEqual([item['label'] for item in data['results']], ['web-tier-prod'])
        result = data['results'][0]
        self.assertIn('East', result['meta'])
        self.assertTrue(result['url'].startswith(reverse('snapshot', args=[snapshot.pk]) + '?browse=1&find=web-tier-prod#'))
        self.assertIn('NSX is not contacted', data['source'])
        self.assertEqual(self.client.get(reverse('navigation-search'), {'q': 'w'}).json()['results'], [])
        self.assertEqual(self.client.get(reverse('navigation-search'), {'q': 'zz-missing'}).json()['results'], [])

    def test_search_endpoint_requires_sign_in_and_get(self):
        self.assertEqual(self.client.get(reverse('navigation-search'), {'q': 'web'}).status_code, 302)
        self.login()
        self.assertEqual(self.client.post(reverse('navigation-search'), {'q': 'web'}).status_code, 405)

    def test_search_ignores_testing_and_unindexed_snapshots(self):
        self.login()
        report = sample_report(); report['objects'][0]['name'] = 'only-in-testing'
        testing = prepare_snapshot(self.environment, dict(report, testing=True)); testing.testing = True; testing.save()
        from .snapshot_index import build
        build(testing)
        self.assertEqual(self.client.get(reverse('navigation-search'), {'q': 'only'}).json()['results'], [])
