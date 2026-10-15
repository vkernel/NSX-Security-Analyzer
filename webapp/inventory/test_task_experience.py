from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.test.utils import CaptureQueriesContext
from django.db import connection
from .models import Environment, Snapshot, SnapshotComparison, SnapshotComparisonRow, AuditJob, FindingRecalculation, Finding, FindingEvent


@override_settings(STORAGES={'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class TaskExperienceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('operator', is_staff=True)
        self.client.force_login(self.user)
        self.env = Environment.objects.create(name='East', slug='east', manager='https://east.example')
        self.before = Snapshot.objects.create(environment=self.env, generated_at=timezone.now()-timedelta(days=1), report={})
        self.after = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report={})
        self.pair = SnapshotComparison.objects.create(before=self.before, after=self.after)

    def test_activity_has_independent_tasks_and_filters_without_reading_reports(self):
        AuditJob.objects.create(environment=self.env, status='running')
        FindingRecalculation.objects.create(environment=self.env, status='queued')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('activity'))
        self.assertEqual(len(response.context['tasks']), 3)
        self.assertContains(response, 'Snapshot comparison')
        self.assertFalse(any('"report"' in q['sql'] or '"config"' in q['sql'] or '"shell"' in q['sql'] for q in queries))
        response = self.client.get(reverse('activity'), {'type':'review','status':'active','environment':self.env.pk})
        self.assertEqual(len(response.context['tasks']), 1)
        self.assertEqual(response.context['tasks'][0]['kind'], 'Finding review indexing')
        self.assertEqual(self.client.get(reverse('activity'), {'environment':'invalid'}).context['tasks'], [])
        self.client.logout()
        self.assertEqual(self.client.get(reverse('activity')).status_code, 302)

    def test_comparison_filters_and_counts_do_not_recompute(self):
        self.pair.ready=True; self.pair.status='completed'; self.pair.save()
        for index, status in enumerate(['Added', 'Removed', 'Presence uncertain']):
            SnapshotComparisonRow.objects.create(comparison=self.pair, ordinal=index, data={'name':'Same name', 'path':'/infra/groups/'+str(index), 'kind':'Group','status':status,'changes':[]})
        params = {'before':self.before.pk,'after':self.after.pk,'change':'Presence uncertain','q':'/infra/groups/2'}
        response = self.client.get(reverse('snapshot-comparison',args=[self.env.pk]), params)
        self.assertEqual(response.context['count'],1)
        self.assertEqual(len(response.context['change_counts']),3)
        params['q']='missing'
        self.assertContains(self.client.get(reverse('snapshot-comparison',args=[self.env.pk]),params), 'No matching differences')

    def test_my_work_shows_assignment_age_stage_and_authoritative_blocker(self):
        finding = Finding.objects.create(environment=self.env, snapshot=self.after, name='Group A', path='/infra/a', kind='empty_group', fingerprint='a', owner=self.user, workflow_state='owner_review', first_seen=timezone.now(), last_seen=timezone.now(), evaluated_at=timezone.now(), qualification='insufficient')
        FindingEvent.objects.create(finding=finding, message='Assigned', details={'action':'assign'})
        response = self.client.get(reverse('my-work'), {'environment':self.env.pk})
        row = response.context['page'][0]
        self.assertIsNotNone(row.assigned_at)
        self.assertEqual(row.readiness['status'],'blocked')
        self.assertContains(response,'Owner review')
        self.assertContains(response,'currently observed and eligible')
        self.assertEqual(response.context['selected_environment'],self.env)
        self.assertEqual(self.client.get(reverse('my-work'),{'environment':'999'}).context['page'].paginator.count,0)

    def test_collection_result_resolves_on_click_and_handles_retention(self):
        job = AuditJob.objects.create(environment=self.env, status='succeeded')
        url = reverse('collection-result', args=[job.pk])
        self.assertRedirects(self.client.get(url), reverse('environment', args=[self.env.pk]), fetch_redirect_response=False)
        self.after.job = job; self.after.save(update_fields=['job'])
        self.assertRedirects(self.client.get(url), reverse('snapshot', args=[self.after.pk]), fetch_redirect_response=False)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code,302)
