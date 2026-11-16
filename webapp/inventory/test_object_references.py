from django.contrib.auth import get_user_model
from django.db import connection
from django.http import Http404
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, SnapshotRecord
from .object_references import read


class ObjectReferenceTests(TestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='References',slug='object-references',manager='https://offline.invalid')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={})
        self.path='/infra/domains/default/groups/web'
        self.rule_paths=[f'/infra/domains/default/security-policies/p/rules/{i:03}' for i in range(53)]
        self.parent='/infra/domains/default/groups/parent'
        self.owner=self.record(1,'inventory','Web',self.path,{'kind':'group','referenced_by':self.rule_paths+[self.parent,self.rule_paths[0]],
            'reference_details':[{'path':self.parent,'name':'Parent group'}]})
        for i,path in enumerate(self.rule_paths):
            self.record(100+i,'dfw',f'Rule {i}',path,{'name':f'Rule {i}','rule_id':6100+i,'category':'Application','policy_name':'Portal',
                'action':'ALLOW','disabled':False,'configuration':{'rule':{'sequence_number':i*10}},
                'source_groups':[self.path],'destination_groups':[],'scope':[self.path],'services':['/infra/services/https']})

    def record(self,ordinal,view,name,path,data):
        return SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=ordinal,view=view,name=name,sort_name=name,
            compact={'path':path},data=dict(data,path=path),columns=[],sort_values={})

    def test_firewall_pages_are_bounded_deduplicated_and_enriched(self):
        from .firewall_reference_context import context_profiles_in_use
        context_profiles_in_use.cache_clear()
        with CaptureQueriesContext(connection) as queries:
            read(self.snapshot.pk,self.path)
        self.assertEqual(len(queries),5) # includes the once-per-snapshot context profile check
        with CaptureQueriesContext(connection) as queries:
            first=read(self.snapshot.pk,self.path)
        self.assertEqual(len(first['items']),25);self.assertTrue(first['has_next'])
        self.assertEqual(first['items'][0]['matched_fields'],['Source','Applied to'])
        self.assertEqual(first['items'][0]['matched_groups'],[{'path':self.path,'name':'Web'}])
        self.assertEqual(first['items'][0]['reference_reason'],'Saved group reference')
        self.assertEqual(first['items'][0]['name'],'Rule 0')
        self.assertEqual(first['items'][0]['sequence_number'],0)
        self.assertEqual(len(queries),4)
        self.assertFalse(any('"report"' in query['sql'] for query in queries))
        last=read(self.snapshot.pk,self.path,page=2)
        self.assertEqual(len(last['items']),3);self.assertFalse(last['has_next'])
        self.assertNotIn('services',first['items'][0])

    def test_non_firewall_references_remain_available_separately(self):
        result=read(self.snapshot.pk,self.path,'objects')
        self.assertEqual(result['items'],[{'path':self.parent,'name':'Parent group','via_group':self.path}])
        self.assertFalse(result['has_next'])

    def test_service_references_use_service_field_and_reason(self):
        service='/infra/services/https'
        self.record(2,'inventory','HTTPS',service,{'kind':'custom_service','referenced_by':self.rule_paths})
        first=read(self.snapshot.pk,service)['items'][0]
        self.assertEqual(first['matched_fields'],['Service'])
        self.assertEqual(first['reference_reason'],'Saved service reference')
        self.assertEqual(first['field_matches'][0]['group'],{'path':service,'name':'HTTPS'})

    def test_missing_rule_context_remains_unknown(self):
        self.owner.data['referenced_by']=['/infra/policies/old/rules/unknown'];self.owner.save()
        item=read(self.snapshot.pk,self.path)['items'][0]
        self.assertIsNone(item['firewall_type']);self.assertNotIn('action',item)
        self.assertEqual(item['matched_fields'],[])

    def test_snapshot_and_category_validation(self):
        other=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={})
        with self.assertRaises(Http404):read(other.pk,self.path)
        with self.assertRaises(Http404):read(self.snapshot.pk,'/missing')
        for section,page in [('invalid',0),('firewall',-1),('objects',1000001)]:
            with self.assertRaises(ValueError):read(self.snapshot.pk,self.path,section,page)

    def test_endpoint_requires_login_and_retains_archived_snapshot_guard(self):
        url=reverse('snapshot-data',args=[self.snapshot.pk]);params={'op':'object-references','path':self.path}
        self.assertEqual(self.client.get(url,params).status_code,302)
        self.client.force_login(get_user_model().objects.create_user('reference-reader'))
        response=self.client.get(url,params);self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json()['items']),25)
        self.snapshot.browsing_status='archived';self.snapshot.save(update_fields=['browsing_status'])
        self.assertEqual(self.client.get(url,params).status_code,409)
