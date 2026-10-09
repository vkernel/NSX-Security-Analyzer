from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, AuditJob, Snapshot, SnapshotComparison

@override_settings(STORAGES={'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class InterfaceConsistencyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('ui-admin', password='test-only')
        self.client.force_login(self.user)
        self.env = Environment.objects.create(name='Example', slug='ui', manager='https://ui.example')

    def test_administration_uses_fixed_tabs_and_separate_schedules(self):
        response = self.client.get(reverse('administration'))
        self.assertContains(response, 'admin-tabs')
        self.assertNotContains(response, 'admin-groups')
        self.assertNotContains(response, '<th>Automatic sync</th>')
        response = self.client.get(reverse('environment-schedules'))
        self.assertContains(response, '<th>Automatic sync</th>')
        self.assertContains(response, 'aria-current="page">Environments &amp; sync')
        response = self.client.get(reverse('workspace-policy'))
        self.assertContains(response, 'aria-current="page">Freshness &amp; notifications')
        self.assertNotContains(response, '>Collection policies<')
        self.user.is_superuser = False; self.user.save()
        response = self.client.get(reverse('administration'))
        self.assertNotContains(response, reverse('review-approvals'))
        self.assertEqual(self.client.get(reverse('environment-schedules')).status_code,200)

    def test_recent_collections_have_one_consistent_table_for_all_states(self):
        for status in ('queued','running','succeeded','failed','cancelled'):
            env = Environment.objects.create(name=status, slug='state-'+status, manager='https://'+status+'.example')
            AuditJob.objects.create(environment=env, status=status)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, '<table class="recent-collections">')
        self.assertContains(response, 'data-label="Environment"', count=5)
        self.assertContains(response, 'data-label="Action"', count=5)
        self.assertContains(response, 'Stopped by request')
        self.assertContains(response, 'Review failure')

    def test_findings_empty_and_filtered_states_differ(self):
        url = reverse('findings', args=[self.env.pk])
        self.assertContains(self.client.get(url), 'No findings ready for review')
        self.assertContains(self.client.get(url, {'q':'nonexistent'}), 'No matching findings')
        self.assertContains(self.client.get(url, {'kind':'invalid'}), 'Check your filters')
        self.assertContains(self.client.get(reverse('my-work'), {'q':'nonexistent'}), 'No matching tasks')

    def test_comparison_states_are_distinct(self):
        before = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report={})
        after = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report={})
        pair = SnapshotComparison.objects.create(before=before, after=after)
        url = reverse('snapshot-comparison', args=[self.env.pk])
        params = {'before':before.pk, 'after':after.pk}
        for state, label in [('queued','Comparison queued'), ('running','Preparing comparison'), ('failed','Comparison failed')]:
            pair.status=state; pair.save(update_fields=['status'])
            response=self.client.get(url,params)
            self.assertContains(response,label)
            self.assertContains(response,'Retry comparison' if state == 'failed' else 'Refresh status')
        pair.ready=True; pair.save(update_fields=['ready'])
        self.assertContains(self.client.get(url,params),'Comparison ready')
