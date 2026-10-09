from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.http import Http404
from django.urls import reverse
from .models import Environment, Snapshot, SnapshotRecord, SnapshotPresentation
from .tag_relationships import read


class TagRelationshipTests(TestCase):
    def setUp(self):
        env = Environment.objects.create(name='Tags', slug='tags', manager='https://nsx.example')
        self.snapshot = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={})
        self.row = SnapshotRecord.objects.create(snapshot=self.snapshot, ordinal=1, view='tags', name='Production', sort_name='production', compact={'name':'Production','scope':'app','vm_count':10000}, data={
            'vms':{f'vm-{i:05}':f'VM {i}' for i in range(10000)}, 'group_conditions':{'/infra/groups/one':'One'},
            'other_assignments':{'/infra/other/1':{'name':'Other','resource_type':'Example'}},
            'notes':['Collection note'], 'condition_evidence_set':0, 'review_conditions':[{'reason':'Legacy definition'}],
            'firewall_references':[{'rule':i,'via_group':'/infra/groups/one','tag_use':'condition'} for i in range(60)]}, columns=[],sort_values={},search_basic='',search_evidence='')
        SnapshotPresentation.objects.create(snapshot=self.snapshot,shell={},tag_evidence={
            'condition_sets':[list(range(60))], 'conditions':[{'name':f'Condition {i}'} for i in range(60)],
            'firewall_rules':[{'name':f'Rule {i}','path':f'/infra/policies/p/rules/{i}','disabled':False,'large_unused_data':'x'*10000} for i in range(60)]})
        SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=2,view='scopes',name='app',sort_name='app',compact={'name':'app','scope':'app','tag_count':60},data={'tags':[{'name':str(i),'vm_count':i,'status':'both'} for i in range(60)]},columns=[],sort_values={},search_basic='',search_evidence='')

    def test_summary_does_not_fetch_full_data(self):
        with CaptureQueriesContext(connection) as queries:
            result=read(self.snapshot.pk,1)
        self.assertEqual(result['data']['vm_count'],10000)
        self.assertNotIn('vms',result['data'])
        self.assertFalse(any('"data"' in q['sql'] or '"report"' in q['sql'] for q in queries))

    def test_every_assignment_remains_accessible_in_bounded_pages(self):
        first=read(self.snapshot.pk,1,'vms')
        second=read(self.snapshot.pk,1,'vms',1)
        last=read(self.snapshot.pk,1,'vms',399)
        self.assertEqual(len(first['items']),25)
        self.assertTrue(first['has_next'])
        self.assertNotEqual(first['items'][0]['path'],second['items'][0]['path'])
        self.assertEqual(last['items'][-1]['path'],'vm-09999')
        self.assertFalse(last['has_next'])
        self.assertEqual(read(self.snapshot.pk,1,'group_conditions')['items'][0]['name'],'One')
        self.assertEqual(read(self.snapshot.pk,1,'other_assignments')['items'][0]['resource_type'],'Example')
        self.assertEqual(read(self.snapshot.pk,1,'notes')['items'],['Collection note'])

    def test_shared_conditions_rules_and_scope_are_paged(self):
        with CaptureQueriesContext(connection) as queries:
            rules=read(self.snapshot.pk,1,'firewall_references',2)
            conditions=read(self.snapshot.pk,1,'condition_evidence',2)
        self.assertEqual(len(rules['items']),10)
        self.assertEqual(rules['items'][0]['name'],'Rule 50')
        self.assertEqual(rules['items'][0]['via_group'],'/infra/groups/one')
        self.assertNotIn('large_unused_data',str(rules))
        self.assertEqual(conditions['items'][0]['name'],'Condition 50')
        self.assertEqual(read(self.snapshot.pk,1,'review_conditions')['items'],[{'reason':'Legacy definition'}])
        self.assertEqual(len(read(self.snapshot.pk,2,'tags',2)['items']),10)
        self.assertFalse(any('SELECT "inventory_snapshotpresentation"."tag_evidence"' in q['sql'] for q in queries))

    def test_sections_and_snapshot_isolation(self):
        for section in ('tags','arbitrary'):
            with self.assertRaises(ValueError):read(self.snapshot.pk,1,section)
        with self.assertRaises(Http404):read(self.snapshot.pk,99)
        with self.assertRaises(ValueError):read(self.snapshot.pk,1,'vms',-1)
        other=Snapshot.objects.create(environment=self.snapshot.environment, generated_at=timezone.now(),report={})
        with self.assertRaises(Http404):read(other.pk,1)

    def test_endpoint_requires_login(self):
        url=reverse('snapshot-data',args=[self.snapshot.pk])
        self.assertEqual(self.client.get(url,{'op':'tag-relationships','id':1}).status_code,302)
        self.client.force_login(get_user_model().objects.create_user('reader'))
        response=self.client.get(url,{'op':'tag-relationships','id':1,'section':'vms'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json()['items']),25)

    def test_normalized_relationships_use_bounded_index_queries(self):
        from .tag_relationships import index_record
        from .snapshot_index import clear_index
        from .models import SnapshotRelationship
        metadata = self.snapshot.presentation.tag_evidence
        expected = {section:read(self.snapshot.pk,1,section,1) for section in ('vms','firewall_references','condition_evidence')}
        index_record(self.row, metadata)
        self.row.relationships_ready = True
        self.row.save(update_fields=['relationships_ready'])
        for section, before in expected.items():
            with CaptureQueriesContext(connection) as queries:
                result = read(self.snapshot.pk,1,section,1)
            self.assertEqual(result, before)
            self.assertEqual(len(queries),2)
            self.assertFalse(any('jsonb_' in q['sql'] for q in queries))
            self.assertLess(len(str(result)),20000)
        clear_index(self.snapshot.pk)
        self.assertFalse(SnapshotRelationship.objects.exists())
