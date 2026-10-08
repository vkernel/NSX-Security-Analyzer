from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, Finding, FindingPolicy, SnapshotFindingAssessment
from .findings import synchronize
from .test_foundations import report


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ObservationTests(TestCase):
    def setUp(self):
        self.env = Environment.objects.create(name='Test', slug='observation', manager='https://nsx.example', sync_interval_minutes=1440)
        self.now = timezone.now()
        self.policy = FindingPolicy.objects.create(empty_group_days=2, minimum_observations=3)
        self.user = get_user_model().objects.create_user('observer', is_superuser=True, is_staff=True)
        self.client.force_login(self.user)

    def collect(self, day, data=None, **kwargs):
        snapshot = Snapshot.objects.create(environment=self.env, generated_at=self.now+timedelta(days=day), report=data or report(), **kwargs)
        synchronize(self.env.pk)
        return snapshot

    def finding(self):
        return Finding.objects.get(environment=self.env, kind='empty_group')

    def test_period_count_idempotence_and_historical_assessment(self):
        first = self.collect(0)
        self.collect(1)
        self.collect(2)
        f = self.finding()
        self.assertEqual((f.qualification, f.observation_days, f.observation_count), ('eligible', 2, 3))
        synchronize(self.env.pk)
        self.assertEqual(self.finding().observation_count, 3)
        self.assertEqual(SnapshotFindingAssessment.objects.get(snapshot=first, kind='empty_group').assessment['status'], 'observing')
        self.policy.empty_group_days = 7
        self.policy.save()
        self.collect(3)
        self.assertEqual((self.finding().qualification, self.finding().observation_count), ('observing', 1))

    def test_unknown_gap_and_configuration_reset(self):
        self.collect(0)
        self.collect(1)
        data = report(); data['objects'][0]['membership'] = 'unknown'
        self.collect(2, data)
        self.assertEqual(self.finding().qualification, 'insufficient')
        self.collect(3)
        self.assertEqual(self.finding().observation_count, 1)
        self.collect(8)
        self.assertEqual(self.finding().observation_count, 1)
        data = report(); data['objects'][0]['unique_id'] = 'replacement'
        self.collect(9, data)
        self.assertEqual(self.finding().observation_count, 1)

    def test_cleared_excluded_and_testing(self):
        self.collect(0)
        data = report(); data['objects'][0]['membership'] = 'nonempty'
        self.collect(1, data)
        self.assertEqual(self.finding().qualification, 'cleared')
        self.collect(2)
        self.collect(3, testing=True)
        self.assertEqual(self.finding().observation_count, 1)
        data = report(); data['objects'][0]['audit_exclusions'] = ['excluded']
        self.collect(4, data)
        self.assertEqual(self.finding().qualification, 'insufficient')

    def test_override_zero_days_and_policy_permissions(self):
        FindingPolicy.objects.create(scope=str(self.env.pk), environment=self.env, empty_group_days=0)
        self.collect(0)
        self.assertEqual(self.finding().qualification, 'eligible')
        url = reverse('finding-policy') + '?environment=' + str(self.env.pk)
        self.assertContains(self.client.get(url), 'Use global defaults')
        self.client.post(url, {'inherit': '1'})
        self.assertFalse(FindingPolicy.objects.filter(environment=self.env).exists())
        self.user.is_superuser = False; self.user.save()
        self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_review_queue_only_contains_eligible_findings(self):
        url = reverse('findings', args=[self.env.pk])
        self.collect(0)
        self.assertEqual(len(self.client.get(url).context['page']), 0)
        self.collect(1); self.collect(2)
        response = self.client.get(url)
        self.assertEqual(len(response.context['page']), 1)
        self.assertNotContains(response, 'Dates and observation duration')
        self.assertNotIn('qualification', response.context['filter_form'].fields)
        f = self.finding(); f.qualification = 'disabled'; f.save()
        self.assertEqual(len(self.client.get(url, {'qualification': 'disabled', 'presence': ''}).context['page']), 0)

    def test_one_day_requires_elapsed_time_and_observations(self):
        self.policy.empty_group_days = 1; self.policy.save()
        self.collect(0); self.collect(0.25); self.collect(0.5)
        self.assertEqual(self.finding().qualification, 'observing')
        self.collect(1)
        self.assertEqual(self.finding().qualification, 'eligible')

    def test_manual_collection_has_nonzero_gap(self):
        self.env.sync_interval_minutes = 0; self.env.save()
        self.policy.empty_group_days = 1; self.policy.save()
        self.collect(0); self.collect(0.5); self.collect(1)
        self.assertEqual(self.finding().qualification, 'eligible')

    def test_zero_days_still_requires_fresh_zero_hit_evidence(self):
        self.policy.zero_hits_days = 0; self.policy.save()
        data = report()
        data['dfw']['rules'] = [{'path': '/rules/zero', 'name': 'Zero', 'disabled': False,
                               'hit_count': 0, 'hit_status': 'zero_hits'}]
        self.collect(0, data)
        self.assertEqual(Finding.objects.get(kind='zero_hits').qualification, 'insufficient')
        data['dfw']['rules'][0]['statistics_checked_at'] = (self.now + timedelta(days=1)).isoformat()
        self.collect(1, data)
        self.assertEqual(Finding.objects.get(kind='zero_hits').qualification, 'eligible')

    def test_filters_and_invalid_sort(self):
        self.collect(0); self.collect(1); self.collect(2)
        url = reverse('findings', args=[self.env.pk])
        response = self.client.get(url, {'qualification': 'eligible', 'kind': 'empty_group', 'sort': 'name', 'direction': 'desc', 'owner': 'none'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['page']), 1)
        response = self.client.get(url, {'sort': 'evidence'})
        self.assertEqual(len(response.context['page']), 0)
        self.assertTrue(response.context['filter_form'].errors)
        self.assertEqual(self.client.get(reverse('finding-detail', args=[self.env.pk, self.finding().pk])).status_code, 200)

    def test_zero_hits_requires_fresh_statistics_and_positive_clears(self):
        self.policy.zero_hits_days = 2; self.policy.save()
        def rules(day, hits=0, checked=None):
            data = report()
            data['dfw']['rules'] = [{'path': '/rules/r', 'name': 'Rule', 'disabled': False,
                'hit_count': hits, 'hit_status': 'zero_hits' if hits == 0 else 'traffic_recorded',
                'statistics_checked_at': (checked or self.now+timedelta(days=day, seconds=-1)).isoformat()}]
            return data
        self.collect(0, rules(0))
        self.collect(1, rules(1))
        self.collect(2, rules(2))
        f = Finding.objects.get(kind='zero_hits')
        self.assertEqual(f.qualification, 'eligible')
        self.collect(3, rules(3, checked=self.now+timedelta(days=1)))
        f.refresh_from_db(); self.assertEqual(f.qualification, 'insufficient')
        self.collect(4, rules(4, hits=20))
        f.refresh_from_db(); self.assertEqual(f.qualification, 'cleared')

    def test_legacy_first_seen_does_not_qualify_and_snapshot_cleanup_keeps_summary(self):
        self.collect(0)
        f = self.finding()
        f.first_seen = self.now-timedelta(days=365)
        f.observation_started = None
        f.save()
        self.collect(1)
        f.refresh_from_db()
        self.assertEqual((f.qualification, f.observation_count), ('observing', 1))
        Snapshot.objects.all().delete()
        f.refresh_from_db()
        self.assertEqual(f.observation_count, 1)
        self.assertFalse(SnapshotFindingAssessment.objects.exists())

    def test_policy_validation_save_and_snapshot_page_queries(self):
        from .forms import FindingPolicyForm
        from .tests import sample_report
        from .snapshot_index import build
        from .models import SnapshotRecord, SnapshotPanel
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        payload = {name: getattr(self.policy, name) for name in FindingPolicyForm.Meta.fields}
        url = reverse('finding-policy')
        payload['minimum_observations'] = 1
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.policy.refresh_from_db(); self.assertEqual(self.policy.minimum_observations, 3)
        payload['minimum_observations'] = 4
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.policy.refresh_from_db(); self.assertEqual(self.policy.minimum_observations, 4)
        data = sample_report()
        data['objects'][0]['membership'] = 'empty'
        snapshot = self.collect(0, data)
        build(snapshot)
        record = SnapshotRecord.objects.filter(snapshot=snapshot, view='inventory').first()
        panel = SnapshotPanel.objects.filter(snapshot=snapshot, members__record=record).first()
        endpoint = reverse('snapshot-data', args=[snapshot.pk])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(endpoint, {'panel': panel.slug})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(r['data']['finding_assessments'] for r in response.json()['rows']))
        self.assertFalse(any('"report"' in q['sql'] for q in queries))
        detail = self.client.get(endpoint, {'op': 'detail', 'id': record.ordinal})
        self.assertEqual(detail.json()['data']['finding_assessments'][0]['status'], 'observing')
        export = self.client.get(endpoint, {'op': 'export', 'panel': panel.slug})
        self.assertIn(b'finding_assessments', b''.join(export.streaming_content))
