import copy
from datetime import timedelta
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, AuditJob
from .history import analyze


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RuleHistoryTests(TestCase):
    def setUp(self):
        self.environment = Environment.objects.create(name='East', slug='east', manager='https://east.example')
        self.end = timezone.now().replace(microsecond=0)
        self.rule = {'path': '/infra/domains/default/security-policies/p/rules/r', 'name': 'Web',
            'rule_id': 1, 'policy_rule_id': 'r', 'unique_id': 'stable-rule', 'created_at': 100,
            'configuration_fingerprint': 'v1', 'disabled': False, 'hit_status': 'zero_hits',
            'hit_count': 0, 'statistics': [{'enforcement_point': 'ep1', 'hit_count': 0}]}

    def save_snapshot(self, days, rule=None, testing=False, errors=None):
        stamp = self.end - timedelta(days=days)
        row = copy.deepcopy(self.rule if rule is None else rule)
        row.setdefault('statistics_checked_at', stamp.isoformat())
        return Snapshot.objects.create(environment=self.environment, generated_at=stamp, testing=testing,
            report={'dfw': {'rules': [row], 'errors': errors or []}})

    def full_window(self):
        for day in range(8):
            self.save_snapshot(day)

    def result(self):
        return analyze(self.environment, 7, self.end)['rows'][0]

    def test_zero_candidate_requires_full_window(self):
        self.full_window()
        result = self.result()
        self.assertEqual(result['status'], 'zero')
        self.assertEqual(result['observations'], 8)
        self.assertEqual(result['span_days'], 7)
        self.assertEqual(result['max_gap_hours'], 24)
        self.assertEqual(result['reasons'], [])

    def test_sparse_or_short_history_is_limited(self):
        self.save_snapshot(7)
        self.save_snapshot(0)
        self.assertEqual(self.result()['status'], 'limited')
        self.environment.snapshots.all().delete()
        self.save_snapshot(0)
        self.assertEqual(self.result()['status'], 'limited')

    def test_positive_packet_count_is_traffic_even_with_zero_hits(self):
        self.full_window()
        rule = copy.deepcopy(self.rule)
        rule['statistics'][0]['packet_count'] = 10
        self.save_snapshot(0.5, rule)
        self.assertEqual(self.result()['status'], 'traffic')
        self.assertEqual(self.result()['positive_observations'], 1)

    def test_rule_recreation_or_config_change_excludes_earlier_series(self):
        for field in ('unique_id', 'configuration_fingerprint'):
            with self.subTest(field=field):
                self.environment.snapshots.all().delete()
                self.full_window()
                newest = self.environment.snapshots.first()
                newest.report['dfw']['rules'][0][field] = 'changed'
                newest.save()
                result = self.result()
                self.assertEqual(result['observations'], 1)
                self.assertEqual(result['status'], 'limited')
                self.assertTrue(any('earlier observations excluded' in reason for reason in result['reasons']))

    def test_legacy_fingerprints_never_qualify(self):
        self.rule.pop('configuration_fingerprint')
        self.full_window()
        self.assertEqual(self.result()['status'], 'limited')

    def test_testing_snapshots_and_duplicate_observations_are_not_extra_evidence(self):
        snapshot = self.save_snapshot(0)
        Snapshot.objects.create(environment=self.environment, generated_at=self.end, report=snapshot.report, imported=True)
        self.save_snapshot(1, testing=True)
        self.assertEqual(self.result()['observations'], 1)

    def test_unknown_and_failed_collections_prevent_zero_candidate(self):
        self.full_window()
        broken = self.environment.snapshots.order_by('generated_at')[3]
        broken.report['dfw']['rules'][0]['hit_status'] = 'unknown'
        broken.save()
        self.assertEqual(self.result()['status'], 'limited')
        self.assertEqual(self.result()['observations'], 7)
        self.environment.snapshots.all().delete()
        self.full_window()
        AuditJob.objects.create(environment=self.environment, status='failed', finished_at=self.end)
        self.assertEqual(self.result()['status'], 'limited')

    def test_counter_decrease_and_enforcement_changes_are_visible(self):
        self.full_window()
        rule = copy.deepcopy(self.rule)
        rule['statistics'][0]['hit_count'] = 5
        self.save_snapshot(0.5, rule)
        self.assertTrue(any('reset' in reason for reason in self.result()['reasons']))
        self.environment.snapshots.all().delete()
        self.full_window()
        rule['statistics'] = [{'enforcement_point': 'ep2', 'hit_count': 0}]
        self.save_snapshot(0.5, rule)
        self.assertEqual(self.result()['status'], 'limited')
        self.assertTrue(any('enforcement' in reason for reason in self.result()['reasons']))

    def test_disabled_rules_and_invalid_counters(self):
        self.rule['disabled'] = True
        self.full_window()
        self.assertEqual(self.result()['status'], 'disabled')
        self.environment.snapshots.all().delete()
        self.rule['disabled'] = False
        self.rule['statistics'][0]['hit_count'] = None
        self.full_window()
        self.assertEqual(self.result()['status'], 'unknown')

    def test_historical_anchor_filters_future_snapshots_and_other_environments(self):
        self.full_window()
        rule = copy.deepcopy(self.rule)
        rule['statistics'][0]['hit_count'] = 10
        self.save_snapshot(-1, rule)
        self.assertEqual(self.result()['status'], 'zero')
        other = Environment.objects.create(name='West', slug='west', manager='https://west.example')
        self.assertEqual(analyze(other, 7, self.end)['rows'], [])

    def test_history_page_requires_login_and_supports_filters(self):
        self.full_window()
        url = reverse('rule-history', args=[self.environment.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user('viewer')
        self.client.force_login(user)
        response = self.client.get(url, {'days': '7', 'snapshot': self.environment.snapshots.first().pk})
        self.assertContains(response, 'Consistently zero')
        self.assertContains(response, 'Web')
        self.assertNotContains(self.client.get(url, {'q': 'absent'}), '<strong>Web</strong>')
        self.assertEqual(self.client.get(url, {'snapshot': 'invalid'}).status_code, 404)
        self.assertEqual(self.client.get(url, {'days': 'invalid'}).status_code, 200)
