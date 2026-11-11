"""Synthetic, multi-page exports, frozen current observations and access boundaries."""
import csv
import gzip
import io
import json
import uuid
from datetime import timedelta
from unittest.mock import patch,MagicMock
import yaml
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase,SimpleTestCase,Client,override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment,Snapshot,SnapshotRecord,ReferenceExport,MembershipRelation,AuditJob
from . import reference_exports as exports
from .reference_export_formats import render

GP='/infra/domains/default/groups/'


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ReferenceExportTests(TransactionTestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='Synthetic exports',slug='reference-exports',manager='https://exports.invalid')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={})
        self.user=get_user_model().objects.create_user('export-owner')
        self.other=get_user_model().objects.create_user('other-export-owner')
        self.client.force_login(self.user)
        self.ordinal=0
        self.path=GP+'shared'
        self.group=self.record('inventory','Shared group',{'kind':'group','path':self.path,'referenced_by':[]})
        self.vm=self.record('vms','Synthetic VM',{'path':'/infra/realized-state/virtual-machines/vm-1','vm_details':{'external_id':'vm-1'},
            'resolved_groups':[{'name':'Shared group','path':self.path,'membership_basis':'direct_vm'}]},
            {'path':'/infra/realized-state/virtual-machines/vm-1','resolved_membership_status':'partial','network_membership_status':'not_applicable','network_membership_version':1})
        self.url=reverse('reference-exports',args=[self.snapshot.pk])

    def record(self,view,name,data,compact=None):
        self.ordinal+=1
        return SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=self.ordinal,view=view,name=name,data=data,
            compact=compact or {'path':data.get('path')},columns=[],sort_values={})

    def rules(self,count=32,groups=None):
        paths=[]
        for i in range(count):
            path='/infra/domains/default/security-policies/p/rules/rule-%03d'%i
            self.record('dfw','Rule %03d'%i,{'name':'Rule %03d'%i,'path':path,'source_groups':groups or [self.path],
                'destination_groups':['ANY'],'scope':['ANY'],'services':['ANY'],'policy_name':'Synthetic policy','category':'Application',
                'rule_id':i,'action':'ALLOW','disabled':False,'configuration':{'rule':{'sequence_number':i}}})
            paths.append(path)
        self.group.data['referenced_by']=paths;self.group.save(update_fields=['data'])
        return paths

    def create(self,**overrides):
        values={'view':'inventory','path':self.path,'section':'all','format':'json','source':'saved_snapshot','request_key':str(uuid.uuid4()),**overrides}
        response=self.client.post(self.url,json.dumps(values),content_type='application/json')
        self.assertEqual(response.status_code,202,response.content)
        return ReferenceExport.objects.get(pk=response.json()['id'])

    def finish(self,job):
        claimed=exports.claim();self.assertEqual(claimed.pk,job.pk)
        with patch('inventory.deletion.connection',side_effect=AssertionError('Exports must never contact NSX')),patch('inventory.current_membership.refresh',side_effect=AssertionError('No refresh')):
            exports.prepare(job.pk)
        job.refresh_from_db();self.assertEqual(job.status,'ready',job.error)
        return gzip.decompress(bytes(job.artifact))

    def test_all_formats_include_every_page_and_are_downloadable(self):
        self.rules()
        for format in ('csv','json','yaml','html','pdf'):
            with self.subTest(format=format):
                job=self.create(format=format)
                content=self.finish(job)
                self.assertIn(b'Rule 031',content) if format!='pdf' else self.assertTrue(content.startswith(b'%PDF-'))
                if format=='json':
                    document=json.loads(content);self.assertEqual(document['sections'][0]['count'],32)
                    self.assertEqual(document['sections'][0]['items'][31]['sequence_number'],31)
                if format=='yaml':self.assertEqual(yaml.safe_load(content)['sections'][0]['count'],32)
                if format=='csv':self.assertEqual(len(list(csv.reader(io.StringIO(content.decode('utf-8-sig'))))),34) # 32 rules + empty other-object coverage
                response=self.client.get(reverse('reference-export-download',args=[job.pk]))
                self.assertEqual(response.status_code,200);self.assertEqual(response.content,content)
                self.assertIn('attachment;',response['Content-Disposition']);self.assertEqual(response['X-Content-Type-Options'],'nosniff')

    def test_multiple_export_batches_preserve_every_rule(self):
        self.rules(501)
        document=json.loads(self.finish(self.create(section='firewall')))
        self.assertEqual(document['sections'][0]['count'],501)
        self.assertEqual(len({item['path'] for item in document['sections'][0]['items']}),501)

    def test_export_heartbeat_is_accepted_by_deployment_probe(self):
        from django.core.management import call_command
        with patch('inventory.management.commands.check_heartbeat.Path') as path:
            path.return_value.stat.return_value.st_mtime=__import__('time').time()
            call_command('check_heartbeat','exports')

    def test_vm_export_preserves_partial_status_and_full_dense_provenance(self):
        groups=[GP+str(i) for i in range(40)]
        self.vm.data['resolved_groups']=[{'path':path,'name':path} for path in groups];self.vm.save()
        self.rules(1,groups)
        document=json.loads(self.finish(self.create(view='vms',id=self.vm.ordinal,path=None,section='confirmed_rules')))
        section=document['sections'][0];self.assertEqual(section['status'],'partial')
        item=section['items'][0];self.assertEqual(len(item['matched_groups']),40);self.assertEqual(len(item['membership_evidence']),40)
        self.assertFalse(item['groups_truncated']);self.assertEqual(item['services'],['ANY'])
        self.assertIn('not an effective-policy',document['metadata']['note'])

    def test_current_observation_is_frozen_and_not_refreshed(self):
        from .current_membership import save
        save(self.env,'vm',{'identity':'vm-1'},[{'path':self.path,'identity':self.path,'name':'Shared'}],'complete',[])
        job=self.create(view='vms',id=self.vm.ordinal,path=None,section='resolved_groups',source='current_cache')
        MembershipRelation.objects.all().delete()
        document=json.loads(self.finish(job))
        self.assertEqual(document['sections'][0]['items'][0]['path'],self.path)
        self.assertEqual(document['metadata']['evidence'],'current_cache')
        self.assertTrue(document['metadata']['current_observation']['checked_at'])
        job.refresh_from_db();self.assertEqual(job.observation,{})
        self.vm.refresh_from_db();self.assertEqual(self.vm.compact['resolved_membership_status'],'partial')

    def test_downloads_and_status_are_owner_only_and_expire(self):
        job=self.create();self.finish(job)
        for route in ('reference-export','reference-export-download'):
            url=reverse(route,args=[job.pk]);self.client.force_login(self.other)
            self.assertEqual(self.client.get(url).status_code,404)
            if route=='reference-export':self.assertEqual(self.client.post(url).status_code,404)
        self.client.force_login(self.user)
        ReferenceExport.objects.filter(pk=job.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.get(reverse('reference-export-download',args=[job.pk])).status_code,410)
        job.refresh_from_db();self.assertIsNone(job.artifact);self.assertEqual(job.status,'expired')

    def test_csrf_validation_idempotency_and_queue_limits(self):
        values={'view':'inventory','path':self.path,'section':'all','format':'json','source':'saved_snapshot','request_key':str(uuid.uuid4())}
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(self.url,json.dumps(values),content_type='application/json').status_code,403)
        first=self.client.post(self.url,json.dumps(values),content_type='application/json');self.assertEqual(first.status_code,202)
        self.assertEqual(self.client.post(self.url,json.dumps(values),content_type='application/json').json()['id'],first.json()['id'])
        for _ in range(2):self.create()
        values['request_key']=str(uuid.uuid4())
        self.assertEqual(self.client.post(self.url,json.dumps(values),content_type='application/json').status_code,429)
        values['section']='related_groups'
        self.assertEqual(self.client.post(self.url,json.dumps(values),content_type='application/json').status_code,400)

    def test_cancelled_and_expired_jobs_cannot_publish(self):
        job=self.create();exports.claim()
        response=self.client.post(reverse('reference-export',args=[job.pk]))
        self.assertEqual(response.json()['status'],'cancelled')
        exports.prepare(job.pk);job.refresh_from_db();self.assertIsNone(job.artifact)
        self.assertEqual(job.status,'cancelled')

    def test_worker_stops_a_cancelled_export_process(self):
        from .management.commands.audit_worker import Command
        job=self.create()
        command=Command();process=MagicMock();process.poll.return_value=None;process.__enter__.return_value=process
        def cancel(): ReferenceExport.objects.filter(pk=job.pk).update(status='cancelled')
        with patch.object(command,'check_lease',side_effect=cancel),patch('inventory.management.commands.audit_worker.subprocess.Popen',return_value=process):
            self.assertTrue(command.run_export())
        process.kill.assert_called_once();process.wait.assert_called_once()
        job.refresh_from_db();self.assertEqual(job.status,'cancelled');self.assertIsNone(job.artifact)

    def test_size_and_row_limits_fail_explicitly_without_partial_download(self):
        self.rules(3)
        job=self.create()
        with patch('inventory.reference_export_data.MAX_ROWS',2):
            exports.claim();exports.prepare(job.pk)
        job.refresh_from_db();self.assertEqual(job.status,'failed');self.assertIsNone(job.artifact)
        self.assertEqual(self.client.get(reverse('reference-export-download',args=[job.pk])).status_code,409)
        job=self.create()
        with patch('inventory.reference_exports.MAX_ARTIFACT',1):
            exports.claim();exports.prepare(job.pk)
        job.refresh_from_db();self.assertEqual(job.status,'failed');self.assertIsNone(job.artifact)

    def test_export_lane_does_not_claim_collection_jobs(self):
        from .management.commands.audit_worker import Command
        job=AuditJob.objects.create(environment=self.env)
        command=Command()
        with patch.object(command,'check_lease'),patch('inventory.management.commands.audit_worker.claim_job',side_effect=AssertionError('Wrong lane')):
            command.run_worker({'lane':'exports','once':True})
        job.refresh_from_db();self.assertEqual(job.status,'queued')

    def test_tag_firewall_exports_retain_full_rule_context(self):
        from .models import SnapshotRelationship
        paths=self.rules(1)
        tag=self.record('tags','app=web',{})
        tag.relationships_ready=True;tag.save(update_fields=['relationships_ready'])
        SnapshotRelationship.objects.create(record=tag,section='firewall_references',position=0,
            data={'path':paths[0],'via_group':self.path,'tag_use':'condition'})
        document=json.loads(self.finish(self.create(view='tags',id=tag.ordinal,path=None,section='firewall_references')))
        rule=document['sections'][0]['items'][0]
        self.assertEqual(rule['services'],['ANY']);self.assertEqual(rule['source_groups'],[self.path])
        self.assertEqual(rule['reference_reason'],'Group membership condition')

    def test_tags_scopes_services_and_empty_coverage_are_exported(self):
        tag=self.record('tags','app=web',{'vms':{'/vm/1':'VM'},'group_conditions':{},'group_assignments':{},'other_assignments':{},'firewall_references':[],'notes':[]})
        scope=self.record('scopes','app',{'tags':[{'name':'web','vm_count':1}]})
        service=self.record('inventory','Custom service',{'kind':'custom_service','path':'/infra/services/custom','referenced_by':[]})
        for view,id,path in [('tags',tag.ordinal,None),('scopes',scope.ordinal,None),('inventory',None,service.compact['path'])]:
            document=json.loads(self.finish(self.create(view=view,id=id,path=path)))
            self.assertTrue(document['sections']);self.assertTrue(document['sections'][0]['notes'])
            self.assertIn('saved_configuration',[section['status'] for section in document['sections']])


class ReferenceFormatTests(SimpleTestCase):
    def document(self):
        return {'schema_version':1,'metadata':{'environment':'Synthetic','object':{'name':'=SUM(1,2)'},'snapshot_id':'uuid',
            'collected_at':'2026-11-11','requested_at':'2026-11-11','prepared_at':'2026-11-11','note':'Partial evidence'},
            'sections':[{'key':'rules','label':'Rules','status':'partial','source':'saved_snapshot','checked_at':None,'notes':['Unknown total'],
                'count':1,'items':[{'name':'<script>alert(1)</script>','path':'=HYPERLINK("bad")','action':'ALLOW','membership_basis':'network_attachment'}]}]}

    def test_csv_formulas_are_neutralized_and_structured_formats_round_trip(self):
        document=self.document()
        cells=list(csv.reader(io.StringIO(render(document,'csv').decode('utf-8-sig'))))[1]
        self.assertEqual(cells[1],"'=SUM(1,2)");self.assertTrue(cells[15].startswith("'=HYPERLINK"))
        self.assertEqual(json.loads(render(document,'json')),document)
        self.assertEqual(yaml.safe_load(render(document,'yaml')),document)

    def test_non_firewall_reports_use_readable_object_columns(self):
        document=self.document();document['sections'][0]['key']='resolved_groups'
        output=render(document,'html').decode()
        self.assertIn('Path / identity',output);self.assertNotIn('<th scope="col">Rule ID</th>',output)
        self.assertTrue(render(document,'pdf').startswith(b'%PDF-'))

    def test_html_escapes_data_and_has_no_external_dependencies(self):
        output=render(self.document(),'html').decode()
        self.assertNotIn('<script>alert(1)</script>',output)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',output)
        self.assertIn('Content-Security-Policy',output);self.assertNotIn('src=',output)
        self.assertIn('Filter references',output)
