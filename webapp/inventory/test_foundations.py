from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, AuditJob, Finding
from .comparison import compare
from .findings import synchronize
from .coverage import dashboard


def report():
    group = {'kind': 'group', 'path': '/groups/a', 'name': 'A', 'usage': 'unused_candidate',
             'membership': 'empty', 'membership_definition': {'definition': {'expression': []}}}
    return {'objects': [group], 'inventory': {'groups': [group], 'services': []},
            'dfw': {'rules': [], 'policies': [], 'errors': []}, 'search_coverage': {'mode': 'all_types'}}


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class FoundationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('reviewer', is_staff=True)
        self.client.force_login(self.user)
        self.env = Environment.objects.create(slug='one', name='One', manager='https://one.example')
        self.now = timezone.now()

    def snapshot(self, data=None, **kwargs):
        return Snapshot.objects.create(environment=self.env, report=data or report(),
            generated_at=kwargs.pop('generated_at', self.now), **kwargs)

    def test_comparison_changes_and_ignores_counters(self):
        old = report()
        old['dfw']['rules'] = [{'path': '/rules/a', 'name': 'R', 'action': 'ALLOW', 'hit_count': 0}]
        new = deepcopy(old)
        new['dfw']['rules'][0]['hit_count'] = 99
        self.assertEqual(compare(old, new), [])
        new['dfw']['rules'][0]['action'] = 'DROP'
        self.assertEqual(compare(old, new)[0]['changes'][0]['field'], 'action')
        new['inventory']['services'].append({'path': '/services/a', 'name': 'S'})
        self.assertEqual({r['status'] for r in compare(old, new)}, {'Added', 'Changed'})

    def test_unknown_is_not_empty_or_removal(self):
        old, new = report(), report()
        new['inventory']['groups'][0]['membership'] = 'unknown'
        self.assertEqual(compare(old, new)[0]['status'], 'Evidence incomplete')
        new['inventory']['groups'][0]['configuration'] = {'expression': []}
        self.assertTrue(all(c['unknown'] for c in compare(old, new)[0]['changes']))
        old['dfw']['rules'] = [{'path': '/rules/a', 'name': 'Rule'}]
        new['dfw']['errors'] = ['Access denied']
        self.assertEqual(next(r for r in compare(old, new) if r['kind'] == 'Rule')['status'], 'Presence uncertain')

    def test_comparison_rejects_cross_environment_reverse_and_testing(self):
        first = self.snapshot(generated_at=self.now-timedelta(days=1))
        second = self.snapshot()
        url = reverse('snapshot-comparison', args=[self.env.pk])
        self.assertContains(self.client.get(url), 'Compare snapshots')
        other = Environment.objects.create(slug='two', name='Two', manager='https://two.example')
        third = Snapshot.objects.create(environment=other, report=report(), generated_at=self.now)
        for params in ({'before': third.pk, 'after': second.pk}, {'before': second.pk, 'after': first.pk}):
            response = self.client.get(url, params)
            self.assertFalse(response.context['form'].is_valid())
        first.testing = True
        first.save()
        self.assertFalse(self.client.get(url, {'before': first.pk, 'after': second.pk}).context['form'].is_valid())

    def test_acknowledgement_preserved_then_reopened(self):
        self.snapshot()
        synchronize(self.env.pk)
        finding = Finding.objects.get(environment=self.env, kind='empty_group')
        finding.status = 'acknowledged'
        finding.owner = self.user
        finding.save()
        before = finding.events.count()
        synchronize(self.env.pk)
        self.assertEqual(finding.events.count(), before)
        self.snapshot(generated_at=self.now+timedelta(minutes=1))
        synchronize(self.env.pk)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'acknowledged')
        new = report()
        new['objects'][0]['membership_definition']['definition']['expression'] = ['different']
        self.snapshot(new, generated_at=self.now+timedelta(minutes=2))
        synchronize(self.env.pk)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'open')
        self.assertEqual(finding.owner, self.user)
        self.assertIn('Reopened', finding.events.first().message)

    def test_disappearing_finding_is_not_resolved_and_reappearance_opens(self):
        self.snapshot()
        synchronize(self.env.pk)
        finding = Finding.objects.get(kind='empty_group')
        finding.status = 'acknowledged'
        finding.save()
        new = report()
        new['objects'][0]['membership'] = 'unknown'
        self.snapshot(new, generated_at=self.now+timedelta(minutes=1))
        synchronize(self.env.pk)
        finding.refresh_from_db()
        self.assertFalse(finding.present)
        self.assertEqual(finding.status, 'acknowledged')
        self.snapshot(generated_at=self.now+timedelta(minutes=2))
        synchronize(self.env.pk)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'open')
        self.assertTrue(finding.present)

    def test_testing_imports_and_older_snapshots_do_not_replace_baseline(self):
        saved = self.snapshot()
        synchronize(self.env.pk)
        self.snapshot(testing=True, generated_at=self.now+timedelta(minutes=1))
        self.snapshot(imported=True, generated_at=self.now+timedelta(minutes=2))
        self.snapshot(generated_at=self.now-timedelta(days=1))
        synchronize(self.env.pk)
        self.assertEqual(set(Finding.objects.values_list('snapshot_id', flat=True)), {saved.pk})

    def test_review_notes_stale_form_and_permissions(self):
        self.snapshot()
        synchronize(self.env.pk)
        finding = Finding.objects.get(kind='empty_group')
        url = reverse('finding-detail', args=[self.env.pk, finding.pk])
        data = {'revision': finding.revision, 'status': 'acknowledged', 'owner': self.user.pk,
                'review_date': '2030-01-01', 'note': '<script>alert(1)</script>'}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertContains(self.client.get(url), '&lt;script&gt;')
        self.assertContains(self.client.post(url, data), 'changed while you were reviewing')
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 200)
        from django.test import Client
        secure = Client(enforce_csrf_checks=True)
        self.user.is_staff = True
        self.user.save()
        secure.force_login(self.user)
        self.assertEqual(secure.post(url, data).status_code, 403)

    def test_retention_preserves_review_history_and_environment_deletion_removes_it(self):
        snapshot = self.snapshot()
        synchronize(self.env.pk)
        finding = Finding.objects.get(kind='empty_group')
        snapshot.delete()
        finding.refresh_from_db()
        self.assertIsNone(finding.snapshot)
        self.assertTrue(finding.events.exists())
        self.assertEqual(self.client.post(reverse('environment-delete', args=[self.env.pk]), {'confirmation': self.env.slug}).status_code, 302)
        self.assertFalse(Finding.objects.exists())

    def test_coverage_counts_failures_unknowns_and_boundary_gaps(self):
        data = report()
        data['objects'][0]['membership'] = 'unknown'
        self.snapshot(data, generated_at=self.now-timedelta(hours=3))
        self.snapshot(testing=True)
        AuditJob.objects.create(environment=self.env, status='failed')
        result = dashboard(self.env, 7, now=timezone.now())
        self.assertEqual(result['snapshots'], 1)
        self.assertEqual(result['failed'].count(), 1)
        self.assertEqual(result['issues'][0]['area'], 'Group membership')
        self.assertEqual(len(result['gaps']), 2)
        self.assertEqual(result['threshold_hours'], 2)
        for name in ('collection-coverage', 'findings'):
            self.assertEqual(self.client.get(reverse(name, args=[self.env.pk])).status_code, 200)

    def test_empty_coverage_is_not_zero_traffic(self):
        result = dashboard(self.env, 30)
        self.assertIsNone(result['latest'])
        self.assertEqual(result['snapshots'], 0)
        self.assertEqual(len(result['gaps']), 1)
        self.assertTrue(result['freshness']['stale'])

    def test_worker_synchronizes_reviews_when_snapshot_is_saved(self):
        from .services import engine, enqueue, claim_job, execute_job
        from .credentials import encrypt_password
        from .tests import sample_report
        self.env.password_ciphertext = encrypt_password('synthetic-password')
        self.env.save()
        data = sample_report()
        data['manager'] = 'one.example'
        data['objects'][0]['membership'] = 'empty'
        job = enqueue(self.env, self.user)
        self.assertEqual(claim_job().pk, job.pk)
        with patch.object(engine(), 'NSXClient') as client, patch.object(engine(), 'audit', return_value=data):
            client.return_value.base_url = 'https://one.example/policy/api/v1'
            execute_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded', job.error)
        self.assertTrue(Finding.objects.filter(environment=self.env, kind='empty_group', snapshot=job.snapshot).exists())

    def test_paused_environment_still_reports_stale_evidence(self):
        self.snapshot(generated_at=self.now-timedelta(days=20))
        self.env.enabled = False
        self.env.save()
        result = dashboard(self.env, 30)
        self.assertTrue(result['stale_data'])
        self.assertEqual(result['freshness']['label'], 'Paused')
