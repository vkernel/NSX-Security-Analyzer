from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Environment, UserPreferences
from .services import prepare_snapshot
from .tests import sample_report


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RedesignTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('design-viewer')
        self.env=Environment.objects.create(name='East',slug='east',manager='https://east.example')
        self.client.force_login(self.user)

    def test_focused_settings_preserve_other_sections_and_reset(self):
        UserPreferences.objects.create(user=self.user,timezone='Europe/Amsterdam',page_size=50,theme='dark')
        url=reverse('website-settings')+'?section=appearance'
        response=self.client.get(url)
        self.assertNotContains(response,'name="timezone"')
        self.assertContains(response,'name="theme"')
        self.assertEqual(self.client.post(url,{'density':'compact','theme':'light','text_size':'large'}).status_code,302)
        saved=UserPreferences.objects.get(user=self.user)
        self.assertEqual(saved.timezone,'Europe/Amsterdam');self.assertEqual(saved.page_size,50)
        self.client.post(url,{'action':'reset'})
        saved.refresh_from_db();self.assertEqual(saved.theme,'system');self.assertEqual(saved.page_size,50)

    def test_environment_switch_preserves_section_and_validated_panel(self):
        snapshot=prepare_snapshot(self.env,sample_report());snapshot.save()
        url=reverse('workspace-section',args=['rules'])
        response=self.client.get(url,{'environment':self.env.pk,'panel':'zero-hit-rules'})
        self.assertEqual(response.url,reverse('snapshot',args=[snapshot.pk])+'#zero-hit-rules')
        response=self.client.get(url,{'environment':self.env.pk,'panel':'https://untrusted.example'})
        self.assertEqual(response.url,reverse('snapshot',args=[snapshot.pk])+'#dfw-rules')
        self.assertEqual(self.client.session['selected_environment'],self.env.pk)

    def test_directory_search_and_permissions(self):
        self.assertContains(self.client.get(reverse('environment-directory'),{'q':'East'}),'East')
        self.assertContains(self.client.get(reverse('environment-directory'),{'q':'Missing'}),'No matching environments')
        response=self.client.get(reverse('dashboard'))
        self.assertNotContains(response,'data-primary="administration"')
        self.assertContains(response,'data-primary="collections"')

    def test_no_snapshot_has_clear_empty_state(self):
        self.assertContains(self.client.get(reverse('workspace-section',args=['firewall'])),'No snapshot available')

    def test_latest_and_historical_snapshot_labels(self):
        one=prepare_snapshot(self.env,sample_report());one.save()
        two=prepare_snapshot(self.env,sample_report());two.save()
        self.assertContains(self.client.get(reverse('snapshot',args=[one.pk])),'HISTORICAL SNAPSHOT')
        self.assertContains(self.client.get(reverse('snapshot',args=[two.pk])),'LATEST SNAPSHOT')
