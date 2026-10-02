from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.http import Http404
from .models import Environment, Snapshot, SnapshotRecord
from .vm_relationships import read


class VMRelationshipTests(TestCase):
    def setUp(self):
        self.env = Environment.objects.create(name='VM test', slug='vm-test', manager='https://nsx.example')
        self.snapshot = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report={})
        self.row = SnapshotRecord.objects.create(snapshot=self.snapshot, ordinal=1, view='vms',name='VM',sort_name='vm',compact={'name':'VM'}, data={
            'tags':[{'tag':str(i)} for i in range(60)], 'related_groups':[],
            'related_rules':[{'path':'/rule/'+str(i), 'name':'Rule '+str(i), 'via_group':'/group/'+str(g), 'services':['/service/1']} for i in range(30) for g in range(3)],
            'vm_details':{'id':'test'}},columns=[],sort_values={},search_basic='',search_evidence='')

    def test_pages_are_bounded_and_legacy_rules_deduplicated(self):
        with CaptureQueriesContext(connection) as queries:
            result=read(self.snapshot.pk,1,'related_rules')
        self.assertEqual(len(result['items']),25)
        self.assertTrue(result['has_next'])
        self.assertNotIn('services',result['items'][0])
        self.assertEqual(len(read(self.snapshot.pk,1,'related_rules',1)['items']),5)
        self.assertEqual(read(self.snapshot.pk,1,'tags',2)['items'],[{'tag':str(i)} for i in range(50,60)])
        self.assertFalse(any('"report"' in q['sql'] for q in queries))
        self.assertTrue(any('LIMIT 26' in q['sql'] for q in queries))

    def test_rule_detail_and_all_group_provenance(self):
        result=read(self.snapshot.pk,1,'rule',path='/rule/1')
        self.assertEqual(result['details']['services'],['/service/1'])
        self.assertNotIn('via_group',result['details'])
        self.assertEqual(read(self.snapshot.pk,1,'via_groups',path='/rule/1')['items'],['/group/0','/group/1','/group/2'])
        self.row.data['related_rules']=[{'path':'/new','name':'New','via_groups':['/a','/b']}]
        self.row.save()
        self.assertEqual(read(self.snapshot.pk,1,'via_groups',path='/new')['items'],['/a','/b'])
        self.assertEqual(read(self.snapshot.pk,1,'vm')['details'],{'id':'test'})

    def test_wrong_snapshot_and_invalid_section(self):
        with self.assertRaises(Http404):read(self.snapshot.pk,99)
        with self.assertRaises(ValueError):read(self.snapshot.pk,1,'arbitrary')

    def test_endpoint_requires_login_and_returns_bounded_data(self):
        from django.urls import reverse
        url = reverse('snapshot-data', args=[self.snapshot.pk])
        params = {'op':'vm-relationships','id':1,'section':'related_rules'}
        self.assertEqual(self.client.get(url,params).status_code,302)
        self.client.force_login(get_user_model().objects.create_user('vm-reader'))
        response = self.client.get(url,params)
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json()['items']),25)
        self.assertNotIn('data',response.json())

    def test_new_index_rows_deduplicate_and_keep_group_paths(self):
        from .collector import iter_vm_inventory_rows
        report = {'tags': {'virtual_machines':[{'path':'/vm'}], 'firewall_rules':[{'path':'/rule','name':'Rule'}],
                 'objects':[{'name':'tag','vms':{'/vm':'VM'},'group_conditions':{'/a':'A','/b':'B'},
                             'firewall_references':[{'tag_use':'condition','rule':0,'via_group':g} for g in ['/a','/b','/a']]}]}}
        row = next(iter_vm_inventory_rows(report))
        self.assertEqual(len(row['related_rules']),1)
        self.assertEqual(row['related_rules'][0]['via_groups'],['/a','/b'])
