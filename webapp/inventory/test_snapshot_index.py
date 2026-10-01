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

    def test_workspace_pages_do_not_fetch_large_payloads(self):
        from .models import AuditJob, Snapshot
        job = AuditJob.objects.create(environment_id=self.snapshot.environment_id,status='succeeded')
        Snapshot.objects.filter(pk=self.snapshot.pk).update(job=job,
            summary={'groups':61,'coverage_issue_keys':['unused-large-payload']*10000})
        routes = [('dashboard',[]),('environment-directory',[]),('environment',[self.snapshot.environment_id]),
                  ('all-collections',[]),('collection-history',[self.snapshot.environment_id]),
                  ('notifications',[]),('api-jobs',[])]
        for name,args in routes:
            with self.subTest(route=name), CaptureQueriesContext(connection) as queries:
                response = self.client.get(reverse(name,args=args))
                self.assertEqual(response.status_code,200)
            for query in queries:
                sql = query['sql']
                if not sql.startswith('SELECT'): continue
                for field in ('report','html','password_ciphertext','ca_certificate','config','diagnostics'):
                    self.assertNotIn('".'+'"'+field+'"',sql)
                # JSON key projections are allowed; selecting the complete summary is not.
                self.assertNotIn('"inventory_snapshot"."summary",',sql)
        response = self.client.get(reverse('environment',args=[self.snapshot.environment_id]))
        self.assertContains(response,'61')

    def test_environment_directory_query_count_is_constant(self):
        from .models import Environment
        with CaptureQueriesContext(connection) as initial:
            self.client.get(reverse('environment-directory'))
        for i in range(6):
            Environment.objects.create(name='Extra '+str(i),slug='extra-'+str(i),manager='https://extra%d.example'%i)
        with CaptureQueriesContext(connection) as expanded:
            response = self.client.get(reverse('environment-directory'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(initial),len(expanded))

    def test_indexed_coverage_matches_original_evidence(self):
        from .coverage import dashboard
        from .models import SnapshotPresentation
        from django.utils import timezone
        now = timezone.now()
        with CaptureQueriesContext(connection) as queries:
            indexed = dashboard(self.snapshot.environment,30,now)
        self.assertFalse(any('"inventory_snapshot"."report",' in q['sql'] for q in queries))
        SnapshotPresentation.objects.filter(snapshot=self.snapshot).delete()
        legacy = dashboard(self.snapshot.environment,30,now)
        self.assertEqual(indexed['issues'],legacy['issues'])

    def test_vm_inventory_pagination_and_relationship_evidence(self):
        report=sample_report()
        report['tags']={'vm_inventory_complete':True,'virtual_machines':[
            {'external_id':'vm-%d'%i,'display_name':'VM %03d'%i,'power_state':'VM_RUNNING'} for i in range(31)],
            'objects':[{'name':'web','scope':'role','path':'tag-web','vms':{'vm-0':'VM 000'},
                'group_conditions':{'/infra/groups/web':'Web group'},'group_assignments':{},
                'firewall_references':[{'rule':0,'via_group':'/infra/groups/web','tag_use':'condition'},
                    {'rule':1,'via_group':'/infra/groups/metadata','tag_use':'assignment'}]}],
            'firewall_rules':[{'path':'/rules/web','name':'Web rule','disabled':False},
                              {'path':'/rules/metadata','name':'Metadata rule','disabled':False}]}
        report['dfw']={'rules':[{'path':'/rules/web','name':'Web rule','services':['/infra/services/https']}], 'policies':[]}
        rows=engine().vm_inventory_rows(report)
        self.assertEqual(len(rows),31)
        self.assertEqual(rows[0]['tag_count'],1)
        self.assertEqual(rows[0]['related_rules'][0]['services'][0]['path'],'/infra/services/https')
        self.assertEqual(len(rows[0]['related_rules']),1)
        # Use ordinary snapshot rendering with the minimal VM-derived rows injected,
        # so the synthetic tag fixture need not duplicate the full tag report schema.
        base=sample_report()
        snapshot=prepare_snapshot(self.snapshot.environment,base)
        snapshot.save()
        with patch.object(engine(),'iter_vm_inventory_rows',return_value=iter(rows)):
            build(snapshot)
        url=reverse('snapshot-data',args=[snapshot.pk])
        with CaptureQueriesContext(connection) as queries:
            page=self.client.get(url,{'panel':'all-vms','size':25}).json()
        self.assertEqual(page['count'],31)
        self.assertEqual(len(page['rows']),25)
        self.assertNotIn('related_rules',page['rows'][0]['data'])
        self.assertFalse(any('"inventory_snapshot"."report"' in q['sql'] for q in queries))
        detail=self.client.get(url,{'op':'detail','id':page['rows'][0]['id']}).json()
        self.assertEqual(detail['data']['related_groups'][0]['name'],'Web group')
        legacy=dict(report,tags=dict(report['tags']))
        legacy['tags'].pop('virtual_machines')
        self.assertEqual(len(engine().vm_inventory_rows(legacy)),1)

    def test_direct_index_matches_legacy_rows_without_serialized_payload(self):
        import re
        report=sample_report()
        report['objects']=[dict(report['objects'][0],name='Group %d'%i,path='/groups/%d'%i,
            notes=['large-evidence-'+'x'*10000]) for i in range(120)]
        legacy=engine().render_html_report(report)
        old=json.loads(re.search(r'id="report-rows">(.*?)</script>',legacy['scripts'],re.S)[1])
        direct=engine().prepare_index_report(report)
        self.assertEqual(direct['_index_rows'],old['rows'])
        self.assertEqual(direct['_index_metadata'],old['tag_evidence'])
        self.assertNotIn('large-evidence-',direct['scripts'])
        self.assertLess(len(direct['scripts']),len(legacy['scripts'])//2)
        snapshot=prepare_snapshot(self.snapshot.environment,report)
        snapshot.save()
        create=SnapshotRecord.objects.bulk_create
        batches=[]
        def bounded(rows,**kwargs):
            batches.append(len(rows))
            self.assertLessEqual(len(rows),50)
            return create(rows,**kwargs)
        with patch.object(engine(),'render_html_report',side_effect=AssertionError('Legacy render called')), patch.object(SnapshotRecord.objects,'bulk_create',side_effect=bounded):
            build(snapshot,snapshot._rendered)
        self.assertGreater(len(batches),1)
        self.assertEqual(SnapshotRecord.objects.filter(snapshot=snapshot).count(),len(old['rows']))

    def test_refresh_deletes_index_without_loading_records_and_rolls_back(self):
        from .snapshot_index import clear_index
        from .models import SnapshotRecordPanel
        from django.db import transaction
        before = SnapshotRecord.objects.filter(snapshot=self.snapshot).count()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                with CaptureQueriesContext(connection) as queries:
                    clear_index(self.snapshot.pk)
                self.assertFalse(SnapshotRecord.objects.filter(snapshot=self.snapshot).exists())
                self.assertFalse(SnapshotRecordPanel.objects.filter(panel__snapshot=self.snapshot).exists())
                raise RuntimeError('Simulated failed rebuild')
        self.assertEqual(SnapshotRecord.objects.filter(snapshot=self.snapshot).count(),before)
        self.assertTrue(SnapshotPresentation.objects.filter(snapshot=self.snapshot).exists())
        for query in queries:
            if query['sql'].startswith('SELECT'):
                self.assertNotIn('inventory_snapshotrecord',query['sql'])
                self.assertNotIn('"report"',query['sql'])

    def test_vm_preparation_inserts_before_consuming_entire_inventory(self):
        snapshot=prepare_snapshot(self.snapshot.environment,sample_report())
        snapshot.save()
        def machines(report):
            for i in range(151):
                if i == 100:
                    self.assertGreater(SnapshotRecord.objects.filter(snapshot=snapshot,view='vms').count(),0)
                yield {'name':'VM %03d'%i,'path':'vm-%d'%i,'power_state':'VM_RUNNING',
                       'tag_count':0,'group_count':0,'related_rules':[],'related_groups':[],'tags':[]}
        with patch.object(engine(),'iter_vm_inventory_rows',side_effect=machines):
            build(snapshot)
        self.assertEqual(SnapshotRecord.objects.filter(snapshot=snapshot,view='vms').count(),151)

    def test_targeted_refresh_preserves_other_snapshots(self):
        from django.core.management import call_command
        other=prepare_snapshot(self.snapshot.environment,sample_report())
        other.save()
        build(other)
        before=list(SnapshotRecord.objects.filter(snapshot=other).values_list('pk',flat=True))
        call_command('index_snapshots',refresh=True,snapshot=self.snapshot.pk,stdout=io.StringIO())
        self.assertEqual(list(SnapshotRecord.objects.filter(snapshot=other).values_list('pk',flat=True)),before)
