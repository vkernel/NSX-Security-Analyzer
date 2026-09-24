import json
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, AuditJob, Snapshot, UserPreferences, WorkspacePolicy
from .forms import PreferencesForm
from .services import prepare_snapshot
from .usability import freshness
from .tests import sample_report


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class UsabilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('reader')
        self.other = get_user_model().objects.create_user('other')
        self.env = Environment.objects.create(name='East', slug='east', manager='https://east.example')
        self.client.force_login(self.user)

    def settings(self, **changes):
        values = {'page_size':20,'report_page_size':25,'density':'comfortable','history_days':30,'refresh_seconds':5,
                  'timezone':'Europe/Amsterdam','date_format':'iso','theme':'dark','text_size':'large',
                  'high_contrast':True,'reduced_motion':True,'remember_tables':True,'remember_menus':True,'landing_page':'overview'}
        return dict(values, **changes)

    def test_preferences_and_local_time(self):
        self.assertEqual(self.client.post(reverse('website-settings'), self.settings()).status_code,302)
        saved=UserPreferences.objects.get(user=self.user)
        self.assertEqual(saved.timezone,'Europe/Amsterdam')
        job=AuditJob.objects.create(environment=self.env,status='failed',finished_at=timezone.now())
        response=self.client.get(reverse('dashboard'))
        self.assertContains(response,'data-theme="dark"')
        self.assertContains(response,'text-large high-contrast reduced-motion')
        self.assertContains(response,'title="'+job.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')+'"')
        self.assertFalse(PreferencesForm(self.settings(timezone='Invalid/Zone')).is_valid())
        self.assertFalse(PreferencesForm(self.settings(theme='invalid')).is_valid())

    def test_landing_validation_and_redirect(self):
        self.assertFalse(PreferencesForm(self.settings(landing_page='activity')).is_valid())
        self.client.post(reverse('website-settings'),self.settings(landing_page='activity',preferred_environment=self.env.pk))
        self.assertRedirects(self.client.get(reverse('landing')),reverse('rule-history',args=[self.env.pk]))
        self.client.force_login(self.other)
        self.assertRedirects(self.client.get(reverse('landing')),reverse('dashboard'))

    def test_interface_state_validation_isolation_and_optout(self):
        url=reverse('interface-preferences')
        value={'controls':{'search':'prod AND web'},'filters':[[1,{'mode':'contains','text':'prod'}]],'columns':[{'index':0,'visible':True}]}
        self.assertEqual(self.client.post(url,json.dumps({'key':'table:1:all-groups','value':value}),content_type='application/json').status_code,200)
        saved=UserPreferences.objects.get(user=self.user)
        self.assertEqual(saved.interface_state['table:1:all-groups'],value)
        self.assertFalse(UserPreferences.objects.filter(user=self.other).exists())
        for invalid in [{'key':'other','value':{}},{'key':'table:x','value':{'filters':[None]}},{'key':'table:x','value':{'columns':[{'index':-1,'visible':True}]}},{'key':'menu:x','value':'open'}]:
            self.assertEqual(self.client.post(url,json.dumps(invalid),content_type='application/json').status_code,400)
        saved.remember_tables=False;saved.save()
        self.client.post(url,json.dumps({'key':'table:2:x','value':value}),content_type='application/json')
        saved.refresh_from_db();self.assertNotIn('table:2:x',saved.interface_state)
        self.assertEqual(self.client.post(url,'x'*32769,content_type='application/json').status_code,400)

    def test_interface_preferences_csrf_and_auth(self):
        from django.test import Client
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(reverse('interface-preferences'),'{}',content_type='application/json').status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('notifications')).status_code,302)

    def test_shared_policy_permissions_and_validation(self):
        self.assertEqual(self.client.post(reverse('workspace-policy'),{'stale_hours':3}).status_code,403)
        self.user.is_staff=True;self.user.is_superuser=True;self.user.save()
        self.assertEqual(self.client.post(reverse('workspace-policy'),{'stale_hours':0}).status_code,200)
        self.assertFalse(WorkspacePolicy.objects.exists())
        self.assertEqual(self.client.post(reverse('workspace-policy'),{'stale_hours':48,'notify_failed':True}).status_code,302)
        self.assertEqual(WorkspacePolicy.objects.get().stale_hours,48)

    def test_freshness_uses_successful_full_collection(self):
        self.assertTrue(freshness(self.env)['stale'])
        now=timezone.now()
        for testing,imported in [(True,False),(False,True)]:
            job=AuditJob.objects.create(environment=self.env,status='succeeded')
            Snapshot.objects.create(environment=self.env,job=job,generated_at=now,report={},testing=testing,imported=imported)
        self.assertTrue(freshness(self.env)['stale'])
        job=AuditJob.objects.create(environment=self.env,status='succeeded')
        snap=Snapshot.objects.create(environment=self.env,job=job,generated_at=now-timedelta(hours=25),report={})
        self.assertTrue(freshness(self.env)['stale'])
        WorkspacePolicy.objects.create(stale_hours=48)
        self.assertFalse(freshness(self.env)['stale'])
        self.env.enabled=False
        self.assertEqual(freshness(self.env)['label'],'Paused')

    def test_notifications_read_is_per_user_and_policy_applies(self):
        AuditJob.objects.create(environment=self.env,status='failed',finished_at=timezone.now())
        data=self.client.get(reverse('notifications')).json()
        self.assertEqual(data['unread'],1)
        self.assertEqual(data['items'][0]['label'],'Collection failed')
        self.client.post(reverse('notifications-read'))
        self.assertEqual(self.client.get(reverse('notifications')).json()['unread'],0)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('notifications')).json()['unread'],1)
        WorkspacePolicy.objects.create(notify_failed=False,notify_completed=False,notify_coverage=False)
        self.assertEqual(self.client.get(reverse('notifications')).json()['items'],[])

    def test_coverage_new_issues_and_legacy_baseline(self):
        report=sample_report();report['manager']=self.env.manager
        report['dfw']={'rules':[],'policies':[],'errors':['Unable to read policies']}
        first=prepare_snapshot(self.env,report);first.save()
        self.assertEqual(first.summary['new_coverage_issues'],1)
        second=prepare_snapshot(self.env,report);second.save()
        self.assertEqual(second.summary['new_coverage_issues'],0)
        report['dfw']['errors'].append('Unable to read domains')
        third=prepare_snapshot(self.env,report)
        self.assertEqual(third.summary['new_coverage_issues'],1)
        Snapshot.objects.all().delete()
        Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report=report,summary={})
        self.assertEqual(prepare_snapshot(self.env,report).summary['new_coverage_issues'],0)

    def test_notifications_limited_and_coverage_only(self):
        WorkspacePolicy.objects.create(notify_completed=False,notify_failed=False,notify_coverage=True)
        for issues in [0,1]:
            job=AuditJob.objects.create(environment=self.env,status='succeeded',finished_at=timezone.now())
            Snapshot.objects.create(environment=self.env,job=job,generated_at=timezone.now(),report={},summary={'new_coverage_issues':issues})
        data=self.client.get(reverse('notifications')).json()
        self.assertEqual(len(data['items']),1)
        self.assertIn('1 new coverage issue',data['items'][0]['label'])
