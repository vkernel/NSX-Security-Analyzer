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
