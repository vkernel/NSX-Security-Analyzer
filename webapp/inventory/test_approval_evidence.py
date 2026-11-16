from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Finding, FindingLiveCheck, Snapshot
from . import approval_evidence as evidence, approval_live as live
from .deletion import clean
from .finding_workflow import approval_readiness

@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ApprovalEvidenceTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('reviewer',is_staff=True)
        self.env=Environment.objects.create(name='Evidence',slug='evidence',manager='https://nsx.example')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={},summary={})
        self.obj={'resource_type':'Group','path':'/infra/domains/default/groups/g','id':'g','unique_id':'uid','_create_time':1,'expression':[]}
        self.finding=Finding.objects.create(environment=self.env,snapshot=self.snapshot,kind='empty_group',path=self.obj['path'],name='Group',present=True,qualification='eligible',first_seen=timezone.now(),last_seen=timezone.now(),evaluated_at=timezone.now(),evidence={'configuration':clean(self.obj),'unique_id':'uid','created_at':1,'membership':'empty','referenced_by':[]})
        self.url=reverse('finding-verification',args=[self.env.pk,self.finding.pk])
        self.client.force_login(self.user)

    def enqueue(self):
        self.assertEqual(self.client.post(self.url).status_code,200)
        return live.claim()

    def execute(self,check,members=None,denied=False):
        client=Mock(inventory_warnings=[])
        client.get.return_value=self.obj
        def items(path):
            if denied:
                from .collector import AuditError
                raise AuditError('Forbidden',status_code=403)
            return members or []
        client.items.side_effect=items
        @contextmanager
        def connection(env): yield client
        with patch('inventory.deletion.connection',connection), patch('inventory.collector.objects',return_value=[]), patch('inventory.collector.verified_configuration_search',return_value=([self.obj],{'mode':'all_types'})),patch('inventory.collector.collect_references',return_value={self.obj['path']:[]}):
            live.run(check.pk)
        check.refresh_from_db()
        return check,client

    def test_saved_empty_vs_unknown_and_approval_changes(self):
        data=evidence.saved(self.finding)
        self.assertIn(('Membership','Confirmed empty'),data['facts'])
        original=deepcopy(self.finding.evidence)
        self.finding.approvals={'owner':{'evidence':original}}
        self.finding.evidence['configuration']['expression']=[{'key':'Tag','value':'scope|tag'}]
        self.finding.evidence['membership']='unknown'
        data=evidence.saved(self.finding)
        self.assertIn(('Membership','Membership unavailable'),data['facts'])
        self.assertTrue(data['changes'])

    def test_queue_deduplicates_and_checks_permissions(self):
        self.enqueue()
        self.assertEqual(self.client.post(self.url).status_code,409)
        self.assertEqual(approval_readiness(self.finding)['status'],'blocked')
        self.user.is_staff=False;self.user.save()
        self.assertEqual(self.client.post(self.url).status_code,403)
        self.assertEqual(self.client.get(self.url).status_code,403)

    def test_success_keeps_observation_and_approvals_unchanged(self):
        check,client=self.execute(self.enqueue())
        self.assertEqual(check.status,'succeeded')
        self.assertIn(['VM members','0'],check.result['facts'])
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.qualification,'eligible')
        self.assertEqual(self.finding.revision,0)
        client.http.request.assert_not_called()

    def test_returning_members_invalidates_approval_and_blocks_review(self):
        self.finding.owner=self.user;self.finding.workflow_state='ready';self.finding.approvals={'owner':{'actor_id':str(self.user.pk)}};self.finding.save()
        check,_=self.execute(self.enqueue(),members=[{'display_name':'VM one','id':'vm1'}])
        self.assertEqual(check.status,'changed')
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.approvals,{})
        self.assertEqual(approval_readiness(self.finding)['status'],'blocked')
        response=self.client.get(self.url,{'section':'virtual-machines','q':'one'})
        self.assertEqual(response.json()['count'],1)

    def test_denied_membership_is_not_empty(self):
        check,_=self.execute(self.enqueue(),denied=True)
        self.assertEqual(check.status,'failed')
        self.assertIn(['Membership','Membership unavailable'],check.result['facts'])
        self.assertTrue(check.result['errors'])

    def test_interruption_is_terminal(self):
        check=self.enqueue();live.interrupted(check.pk);check.refresh_from_db()
        self.assertEqual(check.status,'failed')
        self.assertIsNotNone(check.finished_at)

    def test_collection_race_does_not_invalidate_new_review(self):
        check=self.enqueue()
        Finding.objects.filter(pk=self.finding.pk).update(revision=5)
        check,_=self.execute(check,members=[{'id':'vm1'}])
        self.assertEqual(check.status,'stale')
        self.finding.refresh_from_db();self.assertEqual(self.finding.revision,5)

    def test_panel_and_saved_service_rule_policy_details(self):
        response=self.client.get(reverse('finding-detail',args=[self.env.pk,self.finding.pk]))
        self.assertContains(response,'Evidence for approval')
        self.assertContains(response,'Verify current state')
        for kind,config in [('unused_service',{'service_entries':[{'l4_protocol':'TCP','destination_ports':['443']}]}),('zero_hits',{'rule':{'action':'ALLOW','source_groups':['ANY'],'destination_groups':['g'],'services':['s'],'scope':['ANY']},'policy':{'category':'Application'}}),('empty_policy',{'scope':['ANY'],'category':'Application'})]:
            self.finding.kind=kind;self.finding.evidence={'configuration':config,'rule_count':0}
            self.assertTrue(evidence.saved(self.finding)['sections'])

    def test_incomplete_inventory_never_claims_confirmed_empty(self):
        check=self.enqueue()
        client=Mock(inventory_warnings=[{'endpoint':'/members'}]);client.get.return_value=self.obj;client.items.return_value=[]
        @contextmanager
        def connection(env): yield client
        with patch('inventory.deletion.connection',connection),patch('inventory.collector.objects',return_value=[]),patch('inventory.collector.verified_configuration_search',return_value=([self.obj],{'mode':'all_types'})),patch('inventory.collector.collect_references',return_value={self.obj['path']:[]}): live.run(check.pk)
        check.refresh_from_db()
        self.assertEqual(check.status,'failed')
        self.assertIn(['Membership','Membership unavailable'],check.result['facts'])

    def test_typed_group_skips_only_inapplicable_member_endpoints(self):
        self.obj['expression']=[{'resource_type':'Condition','member_type':'VirtualMachine','key':'Tag','operator':'EQUALS','value':'scope|tag'}]
        self.finding.evidence['configuration']=clean(self.obj);self.finding.save()
        check,client=self.execute(self.enqueue())
        self.assertEqual(check.status,'succeeded')
        self.assertEqual(client.items.call_count,3)
        self.assertIn(['Switch members','Not applicable to this definition'],check.result['facts'])

    def test_live_service_rule_and_policy_reads(self):
        for kind,path,resource in [('unused_service','/infra/services/s','Service'),('disabled','/infra/domains/default/security-policies/p/rules/r','Rule'),('empty_policy','/infra/domains/default/security-policies/p','SecurityPolicy')]:
            with self.subTest(kind=kind):
                self.obj={'path':path,'resource_type':resource,'id':'r','unique_id':'uid','_create_time':1,'disabled':True,'service_entries':[]}
                config=clean(self.obj)
                if resource=='Rule': config={'rule':config,'policy':{k:None for k in ('scope','category','sequence_number','stateful')}}
                self.finding.kind=kind;self.finding.path=path;self.finding.evidence={'configuration':config,'unique_id':'uid','created_at':1,'referenced_by':[]};self.finding.save()
                check,client=self.execute(self.enqueue())
                self.assertEqual(check.status,'succeeded',check.message)
                client.http.request.assert_not_called()

    def test_member_identity_change_detected_after_owner_approval(self):
        self.finding.kind='unused';self.finding.approvals={'owner':{'live_members':{'virtual-machines':{'count':1,'digest':'previous-identities'}}}};self.finding.save()
        check,_=self.execute(self.enqueue(),members=[{'id':'different-vm'}])
        self.assertEqual(check.status,'changed')
        self.assertTrue(any(change['field']=='VM members' for change in check.result['changes']))
