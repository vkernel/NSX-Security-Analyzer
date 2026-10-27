from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Environment
from .services import prepare_snapshot
from .tests import sample_report

@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class EnvironmentScopeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('scope-admin', password='test-only')
        self.client.force_login(self.user)
        self.east = Environment.objects.create(name='East', slug='east', manager='https://east.example')
        self.west = Environment.objects.create(name='West', slug='west', manager='https://west.example')

    def test_global_settings_hide_scope_and_edit_is_fixed(self):
        self.assertNotContains(self.client.get(reverse('review-approvals')), 'id="environment-switcher"')
        response = self.client.get(reverse('environment-edit', args=[self.east.pk]))
        self.assertNotContains(response, 'id="environment-switcher"')
        self.assertContains(response, 'environment-fixed')

    def test_list_switch_keeps_review_and_search_but_resets_pagination(self):
        response = self.client.get(reverse('findings', args=[self.east.pk]), {'review':'second', 'q':'VDI', 'page':'2'})
        self.assertContains(response, f'/environments/{self.west.pk}/findings/?review=second&amp;q=VDI')
        self.assertNotContains(response, 'data-url="/environments/'+str(self.west.pk)+'/findings/?page=')

    def test_overview_scope_filters_cards_and_counts(self):
        response = self.client.get(reverse('dashboard'), {'environment': self.west.pk})
        self.assertEqual(response.context['environment_count'], 1)
        self.assertEqual(response.context['cards'][0]['environment'].pk, self.west.pk)
        self.assertContains(response, 'All environments')
        self.assertEqual(self.client.get(reverse('dashboard')).context['environment_count'], 2)

    def test_snapshot_record_fixed_browsing_switch_preserves_panel(self):
        snapshot = prepare_snapshot(self.east, sample_report()); snapshot.save()
        url = reverse('snapshot', args=[snapshot.pk])
        self.assertNotContains(self.client.get(url), 'id="environment-switcher"')
        response = self.client.get(url, {'browse':'1'})
        self.assertContains(response, 'data-browsing="true"')
        self.assertContains(response, f'/workspace/inventory/?environment={self.west.pk}')
        response = self.client.get(reverse('workspace-section', args=['services']), {'environment':self.east.pk})
        self.assertEqual(response.url, url+'?browse=1#all-services')

    def test_availability_check_does_not_change_remembered_scope(self):
        session = self.client.session; session['selected_environment'] = self.east.pk; session.save()
        self.client.get(reverse('dashboard'), {'environment':self.west.pk}, HTTP_X_ENVIRONMENT_CHECK='1')
        self.assertEqual(self.client.session['selected_environment'], self.east.pk)
        self.client.get(reverse('workspace-section', args=['inventory']), {'environment':self.west.pk}, HTTP_X_ENVIRONMENT_CHECK='1', follow=True)
        self.assertEqual(self.client.session['selected_environment'], self.east.pk)
