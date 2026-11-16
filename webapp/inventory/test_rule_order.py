"""Rules and policies in configured NSX order, from saved snapshot records only."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Environment, Snapshot, SnapshotRecord
from .policy_order import read as policies
from .rule_order import read as rules

DOMAIN = '/infra/domains/default'


class RuleOrderTests(TestCase):
    def setUp(self):
        self.env = Environment.objects.create(name='Order', slug='rule-order', manager='https://order.invalid')
        self.snapshot = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report={})
        self.ordinal = 0
        self.group('web', 'WEB')
        self.policy('app', 'App policy', 'Application', 100, [DOMAIN + '/groups/web'])
        self.policy('emergency', 'Isolation', 'Emergency', 10, [])
        self.policy('legacy', 'Legacy', 'Application', None, None)
        self.rule('app', 'App policy', 'Application', 100, 'second', 20, 'ALLOW', sources=[DOMAIN + '/groups/web'], excluded=True)
        self.rule('app', 'App policy', 'Application', 100, 'first', 10, 'DROP', disabled=True)
        self.rule('emergency', 'Isolation', 'Emergency', 10, 'quarantine', 5, 'DROP')
        self.rule('legacy', 'Legacy', 'Application', None, 'old ftp', None, 'ALLOW')

    def record(self, view, name, data, compact):
        self.ordinal += 1
        return SnapshotRecord.objects.create(snapshot=self.snapshot, ordinal=self.ordinal, view=view, name=name, sort_name=name.casefold(),
                                             compact=compact, data=data, columns=[], sort_values={}, search_basic=name, search_evidence='')

    def group(self, key, name):
        path = DOMAIN + '/groups/' + key
        self.record('inventory', name, {'kind': 'group', 'name': name, 'path': path}, {'kind': 'group', 'name': name, 'path': path})

    def policy(self, key, name, category, sequence, scope):
        path = DOMAIN + '/security-policies/' + key
        configuration = {'path': path, 'category': category, 'stateful': True}
        if sequence is not None: configuration['sequence_number'] = sequence
        if scope is not None: configuration['scope'] = scope
        self.record('dfw', name, {'name': name, 'path': path, 'category': category, 'rule_count': 2 if key == 'app' else 1,
                                  'status': 'has_rules', 'configuration': configuration}, {'name': name, 'path': path})

    def rule(self, key, policy, category, policy_sequence, name, sequence, action, sources=None, excluded=False, disabled=False):
        policy_path = DOMAIN + '/security-policies/' + key
        path = policy_path + '/rules/' + name.replace(' ', '-')
        rule = {'sources_excluded': excluded}
        if sequence is not None: rule['sequence_number'] = sequence
        policy_config = {'scope': [DOMAIN + '/groups/web'] if key == 'app' else []}
        if policy_sequence is not None: policy_config['sequence_number'] = policy_sequence
        self.record('dfw', name, {'name': name, 'path': path, 'category': category, 'policy_name': policy, 'policy_path': policy_path,
                                  'rule_id': 1000 + self.ordinal, 'action': action, 'disabled': disabled,
                                  'source_groups': sources or ['ANY'], 'destination_groups': ['ANY'], 'services': ['ANY'], 'scope': ['ANY'],
                                  'configuration': {'rule': rule, 'policy': policy_config}}, {'name': name, 'path': path})

    def test_rules_follow_category_policy_and_rule_sequence_without_policy_records(self):
        data = rules(self.snapshot.pk)
        self.assertEqual([item['name'] for item in data['items']], ['quarantine', 'first', 'second', 'old ftp'])
        self.assertEqual(data['total'], 4)
        first = data['items'][1]
        self.assertEqual((first['nsx_order']['category'], first['nsx_order']['policy_count'], first['nsx_order']['category_count']), ('Application', 2, 3))
        self.assertTrue(first['disabled'])
        second = data['items'][2]
        self.assertTrue(second['rule_fields']['sources']['excluded'])
        self.assertEqual(second['rule_fields']['sources']['items'][0]['name'], 'WEB')
        # A policy Applied To is shown as inherited from the policy.
        self.assertEqual(second['rule_fields']['applied_to']['origin'], 'policy')
        self.assertNotIn('reference_reason', second)

    def test_filters_and_validation(self):
        self.assertEqual([i['name'] for i in rules(self.snapshot.pk, category='Emergency')['items']], ['quarantine'])
        self.assertEqual(rules(self.snapshot.pk, action='DROP')['total'], 2)
        self.assertEqual([i['name'] for i in rules(self.snapshot.pk, query='ftp')['items']], ['old ftp'])
        self.assertEqual(rules(self.snapshot.pk, query='100%')['total'], 0)
        self.assertEqual([i['name'] for i in rules(self.snapshot.pk, policy=DOMAIN + '/security-policies/app')['items']], ['first', 'second'])
        for bad in ({'category': 'Other'}, {'action': 'DELETE'}):
            with self.assertRaises(ValueError):
                rules(self.snapshot.pk, **bad)
        with self.assertRaises(ValueError):
            rules(self.snapshot.pk, -1)

    def test_policies_in_order_with_counts_and_applied_to_states(self):
        items = policies(self.snapshot.pk)['items']
        self.assertEqual([item['name'] for item in items], ['Isolation', 'App policy', 'Legacy'])
        app = items[1]
        self.assertEqual((app['saved_rules'], app['disabled_rules'], app['sequence']), (2, 1, 100))
        self.assertEqual(app['applied_to']['items'][0]['name'], 'WEB')
        self.assertTrue(items[0]['applied_to']['any'])          # empty scope: whole DFW
        self.assertFalse(items[2]['applied_to']['recorded'])    # missing scope: not recorded, not DFW
        self.assertIsNone(items[2]['sequence'])

    def test_snapshot_data_endpoints_require_login_and_read_saved_records(self):
        url = reverse('snapshot-data', args=[self.snapshot.pk])
        self.assertEqual(self.client.get(url, {'op': 'rule-order'}).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user('order-viewer'))
        response = self.client.get(url, {'op': 'rule-order', 'policy': DOMAIN + '/security-policies/app'})
        self.assertEqual([item['name'] for item in response.json()['items']], ['first', 'second'])
        self.assertEqual(self.client.get(url, {'op': 'rule-order', 'action': 'DELETE'}).status_code, 400)
        self.assertEqual(len(self.client.get(url, {'op': 'policy-order'}).json()['items']), 3)
