"""Firewall Summary figures and page: saved evidence only, same states as the report."""
from datetime import timedelta

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .collector import dfw_figures
from .firewall_overview import firewall_overview
from .models import Environment, Snapshot, SnapshotCoverage, SnapshotCoverageIssue


def rule(name, action='ALLOW', sources=('/g/a',), destinations=('/g/b',), services=('/s/http',), scope=('/g/a',),
         disabled=False, hit='traffic_recorded', category='Application'):
    return {'path': '/infra/domains/default/security-policies/p/rules/' + name, 'name': name, 'action': action,
            'source_groups': list(sources), 'destination_groups': list(destinations), 'services': list(services),
            'scope': list(scope), 'disabled': disabled, 'hit_status': hit, 'category': category}


DFW = {'policies': [{'path': '/p', 'category': 'Application', 'status': 'has_rules'},
                    {'path': '/q', 'category': 'Emergency', 'status': 'empty'}],
       'rules': [rule('specific'), rule('any-source', sources=('ANY',), scope=('ANY',), hit='zero_hits'),
                 rule('any-both', sources=('ANY',), destinations=('ANY',), services=('ANY',), hit='unknown'),
                 rule('missing-service', services=(), hit='not_supported'), rule('drop', action='DROP'),
                 rule('off', disabled=True)], 'errors': []}


class FigureTests(SimpleTestCase):
    def test_counts_prompts_actions_and_categories(self):
        data = dfw_figures(DFW)
        self.assertEqual((data['policies'], data['rules'], data['enabled'], data['disabled']), (2, 6, 5, 1))
        self.assertEqual((data['traffic'], data['zero_hits'], data['unknown_activity'], data['empty_policies']), (2, 1, 1, 1))
        self.assertEqual((data['allows'], data['bounded']), (4, 2))
        prompts = {item['key']: item['count'] for item in data['prompts']}
        self.assertEqual(prompts, {'any_endpoint': 2, 'any_both': 1, 'any_service': 1, 'any_scope': 1,
                                   'unknown_service': 1, 'unknown_scope': 0})
        self.assertEqual(data['actions'], [{'action': 'ALLOW', 'count': 4}, {'action': 'DROP', 'count': 1}])
        self.assertEqual(data['categories'][0], {'category': 'Application', 'policies': 1, 'enabled': 5, 'disabled': 1})
        self.assertFalse(data['incomplete'])

    def test_testing_and_partial_evidence_withhold_the_indicator(self):
        self.assertTrue(dfw_figures(DFW, testing=True)['incomplete'])
        self.assertTrue(dfw_figures(dict(DFW, errors=['policy read failed']))['incomplete'])
        self.assertTrue(dfw_figures(dict(DFW, policies=[{'path': '/p', 'status': 'unknown'}]))['incomplete'])
        self.assertEqual(dfw_figures({})['rules'], 0)


class PageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.env = Environment.objects.create(name='Firewall summary', slug='firewall-summary', manager='https://fw.invalid')
        self.snapshot = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report={'dfw': DFW},
                                                summary={'firewall': dfw_figures(DFW)})

    def test_indicator_cards_and_review_rows(self):
        data = firewall_overview(self.snapshot)
        self.assertEqual(data['segmentation'], {'state': 'assessed', 'label': '50.0%', 'value': 50.0})
        cards = {card['label']: card for card in data['cards']}
        self.assertEqual(cards['Rules']['note'], '5 enabled · 1 disabled')
        self.assertTrue(cards['Zero recorded hits']['url'].endswith('#zero-hit-rules'))
        review = {row['key']: row for row in data['review']}
        self.assertEqual(review['disabled']['count'], 1)
        self.assertTrue(review['empty_policies']['url'].endswith('#empty-policies'))
        self.assertEqual([row['width'] for row in data['actions']], [100, 25])

    def test_older_snapshot_without_saved_figures_uses_the_saved_index(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from .models import SnapshotRecord
        older = Snapshot.objects.create(environment=self.env, generated_at=timezone.now() - timedelta(days=1),
                                        report={'dfw': DFW}, summary={'evidence_coverage': []})
        records = [dict(item, rule_id=index) for index, item in enumerate(DFW['rules'])] + [
            dict(policy, rule_count=1) for policy in DFW['policies']]
        for ordinal, item in enumerate(records):
            compact = {key: item[key] for key in ('action', 'rule_count', 'category', 'status') if key in item}
            SnapshotRecord.objects.create(snapshot=older, ordinal=ordinal, view='dfw', name=item['path'], sort_name=item['path'],
                                          compact=compact, data=item, columns=[], sort_values={}, search_basic='', search_evidence='')
        with CaptureQueriesContext(connection) as queries:
            data = firewall_overview(older)['data']
        self.assertEqual({key: data[key] for key in ('rules', 'policies', 'bounded', 'allows', 'empty_policies')},
                         {key: dfw_figures(DFW)[key] for key in ('rules', 'policies', 'bounded', 'allows', 'empty_policies')})
        self.assertFalse(any('"report"' in query['sql'] for query in queries), 'The page never reads report JSON.')
        cache.clear()
        Snapshot.objects.filter(pk=older.pk).update(summary={'evidence_coverage': [{'scope': 'dfw', 'kinds': []}]})
        self.assertTrue(firewall_overview(older)['data']['incomplete'])

    def test_missing_coverage_and_incomplete_evidence_raise_the_callout(self):
        self.assertTrue(firewall_overview(self.snapshot)['coverage']['needs_review'], 'Unrecorded coverage is not complete coverage.')
        saved = SnapshotCoverage.objects.create(snapshot=self.snapshot, issue_count=1)
        self.assertFalse(firewall_overview(self.snapshot)['coverage']['needs_review'])
        SnapshotCoverageIssue.objects.create(coverage=saved, ordinal=0, area='Rule counters', name='x', detail='y')
        self.assertEqual(firewall_overview(self.snapshot)['coverage']['issues'], '1 issue')
        testing = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report={'dfw': DFW},
                                          summary={'firewall': dfw_figures(DFW, testing=True)}, testing=True)
        SnapshotCoverage.objects.create(snapshot=testing, issue_count=0)
        data = firewall_overview(testing)
        self.assertEqual(data['segmentation']['label'], 'Not assessed')
        self.assertTrue(data['coverage']['needs_review'])

    def test_no_allow_rules_is_not_applicable(self):
        empty = {'policies': [], 'rules': [rule('drop', action='DROP')], 'errors': []}
        self.snapshot.summary = {'firewall': dfw_figures(empty)}; self.snapshot.save()
        self.assertEqual(firewall_overview(self.snapshot)['segmentation']['label'], 'N/A')
