import csv
import io
import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from .models import Environment, SnapshotPanel, SnapshotPresentation, SnapshotRecord
from .services import prepare_snapshot, engine
from .snapshot_index import build
from .tests import sample_report


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class IndexedReportTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('reader'))
        env = Environment.objects.create(name='Synthetic', slug='synthetic', manager='https://east.example')
        report = sample_report()
        report['objects'] = [dict(report['objects'][0], name='Group %03d'%i, path='/infra/groups/%d'%i,
                            notes=['Evidence %03d'%i], referenced_by=['/infra/rules/%d'%i]) for i in range(61)]
        report['groups_scanned'] = 61
        self.snapshot = prepare_snapshot(env,report)
        self.snapshot.save()
        build(self.snapshot,self.snapshot._rendered)
        self.url = reverse('snapshot-data',args=[self.snapshot.pk])

    def test_first_load_and_sections_do_not_fetch_original_report_or_records(self):
        with patch.object(engine(),'render_html_report',side_effect=AssertionError('Must not render')):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(reverse('snapshot',args=[self.snapshot.pk]))
                section = self.client.get(self.url,{'op':'section','panel':'all-groups'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-lazy-panel')
        self.assertNotContains(response,'Evidence 060')
        self.assertIn('data-server-table="all-groups"',section.json()['html'])
        self.assertFalse(any('"report"' in q['sql'] or 'inventory_snapshotrecord"' in q['sql'] for q in queries))

    def test_pagination_sorting_and_evidence_are_isolated(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url,{'panel':'all-groups','size':25,'page':1,'order':'desc'}).json()
        self.assertEqual(response['count'],61)
        self.assertEqual(len(response['rows']),25)
        self.assertEqual(response['rows'][0]['data']['name'],'Group 035')
        self.assertNotIn('notes',response['rows'][0]['data'])
        self.assertFalse(any('"report"' in q['sql'] or '"data"' in q['sql'] for q in queries))
        row = response['rows'][0]
        detail = self.client.get(self.url,{'op':'detail','id':row['id']}).json()
        self.assertEqual(detail['data']['notes'],['Evidence 035'])
        self.assertEqual(self.client.get(self.url,{'op':'detail','id':999999}).status_code,404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)

    def test_search_filters_values_and_full_export(self):
        query={'panel':'all-groups','q':'Group AND 0','evidence':'0','sort':'name'}
        self.assertEqual(self.client.get(self.url,query).json()['count'],61)
        filters=json.dumps([[0,{'text':'Group 00','mode':'contains'}]])
        filtered=self.client.get(self.url,dict(query,filters=filters)).json()
        self.assertEqual(filtered['count'],10)
        values=self.client.get(self.url,dict(query,op='values',column=2)).json()['values']
        self.assertEqual(values,[{'value':'Referenced','count':61}])
        response=self.client.get(self.url,dict(query,op='export'))
        rows=list(csv.reader(io.StringIO(b''.join(response.streaming_content).decode('utf-8-sig'))))
        self.assertEqual(len(rows),62)
        self.assertIn('Evidence 060',rows[-1][-1])
        evidence=self.client.get(self.url,{'panel':'all-groups','q':'Evidence 060','evidence':'1'}).json()
        self.assertEqual(evidence['count'],1)
        self.assertEqual(self.client.get(self.url,{'panel':'all-groups','q':'Group AND'}).status_code,400)
        self.assertEqual(self.client.get(self.url,{'panel':'all-groups','q':'[','syntax':'regex'}).status_code,400)

    def test_index_is_atomic_and_idempotent(self):
        before=SnapshotRecord.objects.count()
        build(self.snapshot)
        self.assertEqual(SnapshotRecord.objects.count(),before)
        self.snapshot.presentation.delete()
        SnapshotPanel.objects.filter(snapshot=self.snapshot).delete()
        SnapshotRecord.objects.filter(snapshot=self.snapshot).delete()
        with patch.object(SnapshotPresentation.objects,'create',side_effect=RuntimeError('simulated failure')):
            with self.assertRaises(RuntimeError): build(self.snapshot,self.snapshot._rendered)
        self.assertFalse(SnapshotRecord.objects.filter(snapshot=self.snapshot).exists())
        self.assertFalse(SnapshotPanel.objects.filter(snapshot=self.snapshot).exists())

    def test_navigation_reads_only_display_metadata_with_large_history(self):
        # Summary may contain thousands of coverage fingerprints; it is not dropdown metadata.
        from .models import Snapshot
        Snapshot.objects.filter(pk=self.snapshot.pk).update(summary={'coverage_issue_keys':['x'*64]*10000})
        for _ in range(3):
            Snapshot.objects.create(environment_id=self.snapshot.environment_id,
                generated_at=self.snapshot.generated_at, summary={'coverage_issue_keys':['x'*64]*10000},
                report={'not_needed':'never transfer this'}, html='not needed')
        with CaptureQueriesContext(connection) as queries:
            redirect_response = self.client.get(reverse('workspace-section', args=['inventory']),
                                                {'environment':self.snapshot.environment_id})
            response = self.client.get(reverse('snapshot',args=[self.snapshot.pk]))
        self.assertEqual(redirect_response.status_code,302)
        self.assertEqual(response.status_code,200)
        forbidden = ['"summary"','"report"','"html"','"password_ciphertext"','"ca_bundle_pem"']
        for query in queries:
            if query['sql'].startswith('SELECT'):
                for field in forbidden:
                    self.assertNotIn(field, query['sql'])
        self.assertEqual(len(response.context['history']),4)

    def test_signed_count_reuse_and_changed_search(self):
        first = self.client.get(self.url, {'panel':'all-groups'}).json()
        with CaptureQueriesContext(connection) as queries:
            page = self.client.get(self.url, {'panel':'all-groups', 'page':1,
                'count_token':first['count_token']}).json()
        self.assertEqual(page['count'],61)
        self.assertFalse(any('COUNT(' in q['sql'] for q in queries))
        changed = self.client.get(self.url, {'panel':'all-groups', 'q':'Group 060',
            'count_token':first['count_token']}).json()
        self.assertEqual(changed['count'],1)
        tampered = self.client.get(self.url, {'panel':'all-groups',
            'count_token':first['count_token']+'invalid'}).json()
        self.assertEqual(tampered['count'],61)

    def test_evidence_suggestions_do_not_aggregate_large_payloads(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, {'panel':'all-groups','op':'values','column':4}).json()
        self.assertEqual(response['values'],[])
        self.assertTrue(response['message'])
        self.assertFalse(any('GROUP BY' in q['sql'] for q in queries))

    def test_tag_evidence_projects_only_requested_paths(self):
        from .tag_evidence import for_tag, coverage
        SnapshotPresentation.objects.filter(snapshot=self.snapshot).update(tag_evidence={
            'conditions':[{'name':'needed'},{'name':'unrelated'}],
            'condition_sets':[[0],[1]], 'firewall_rules':[{'name':'rule'},{'name':'unrelated'}],
            'unsupported_conditions':[0], 'unmatched_conditions':[], 'firewall_reference_note':'note'})
        with CaptureQueriesContext(connection) as queries:
            result = for_tag(self.snapshot.pk, {'condition_evidence_set':0,
                'firewall_references':[{'rule':0}]})
            issues = coverage(self.snapshot.pk)
        self.assertEqual(result['conditions'],{0:{'name':'needed'}})
        self.assertEqual(result['firewall_rules'],{0:{'name':'rule'}})
        self.assertNotIn('unrelated',str(result)+str(issues))
        self.assertNotIn('firewall_rules',issues)
        self.assertTrue(all('#>' in q['sql'] or '->' in q['sql'] for q in queries))
