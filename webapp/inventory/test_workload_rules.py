from contextlib import contextmanager
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, SnapshotRecord, FirewallRuleProposal
from .workload_rules import addresses, service_match, investigate


class ServiceMatchingTests(SimpleTestCase):
    def test_addresses_do_not_include_gateways_or_names(self):
        self.assertEqual(addresses({'guest_info':{'ip_addresses':['10.0.0.1','bad'],'gateway':'10.0.0.254'},'display_name':'10.9.9.9'}),['10.0.0.1'])
    def test_ports_nested_missing_and_cycles_are_distinct(self):
        service={'/sql':{'service_entries':[{'resource_type':'L4PortSetServiceEntry','l4_protocol':'TCP','destination_ports':['1430-1435']}]}}
        self.assertEqual(service_match({'services':['/sql']},service,'TCP','1433')[0],'candidate')
        self.assertEqual(service_match({'services':['/sql']},service,'UDP','1433')[0],'no_match')
        self.assertEqual(service_match({'services':['/missing']},service,'TCP','1433')[0],'unknown')
        service['/nested']={'service_entries':[{'resource_type':'NestedServiceServiceEntry','nested_service_path':'/sql'}]}
        self.assertEqual(service_match({'services':['/nested']},service,'TCP','1433')[0],'candidate')
        service['/nested']['service_entries'][0]['nested_service_path']='/nested'
        self.assertEqual(service_match({'services':['/nested']},service,'TCP','1433')[0],'unknown')


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WorkloadInvestigationTests(TestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='Rotterdam',slug='workload-test',manager='https://nsx.example')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={})
        self.user=get_user_model().objects.create_user('planner',password='test-only')
        self.client.force_login(self.user);self.ordinal=0
        self.group_a='/infra/domains/default/groups/App';self.group_b='/infra/domains/default/groups/Data'
        for name,path,identity in [('App',self.group_a,'vm-app'),('Data',self.group_b,'vm-db')]:
            self.row({'name':name,'path':path,'kind':'group','configuration':{'expression':[{'resource_type':'ExternalIDExpression','member_type':'VirtualMachine','external_ids':[identity]}]}})
        self.src=self.row({'name':'app-01','path':'vm-app','vm_details':{'external_id':'vm-app','guest_info':{'ip_addresses':['10.0.0.1']}},'tags':[{'scope':'Application','tag':'Portal'}],'related_groups':[]},'vms')
        self.dst=self.row({'name':'db-01','path':'vm-db','vm_details':{'external_id':'vm-db','guest_info':{'ip_addresses':['10.1.0.1']}}},'vms')
        self.row({'name':'SQL','path':'/infra/services/sql','kind':'service','configuration':{'service_entries':[{'resource_type':'L4PortSetServiceEntry','l4_protocol':'TCP','destination_ports':['1433']}]}})
        self.rule=self.row({'name':'Allow SQL','path':'/infra/domains/default/security-policies/app/rules/sql','policy_path':'/infra/domains/default/security-policies/app','policy_name':'Application','category':'Application','configuration':{'policy':{'sequence_number':10},'rule':{'source_groups':[self.group_a],'destination_groups':[self.group_b],'services':['/infra/services/sql'],'scope':['ANY'],'action':'ALLOW','sequence_number':20,'direction':'IN_OUT'}}})
        self.query={'snapshot':str(self.snapshot.pk),'source':'vm-app','destination':'vm-db','protocol':'TCP','port':'1433'}
        self.url=reverse('segmentation-pair',args=[self.env.pk])
        self.draft_url=reverse('segmentation-proposal-new',args=[self.env.pk])+'?'+__import__('urllib.parse',fromlist=['urlencode']).urlencode(self.query)
    def row(self,data,view='inventory'):
        self.ordinal+=1
        return SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=self.ordinal,view=view,name=data['name'],sort_name=data['name'],data=data,compact={},columns=[],sort_values={},search_basic=data['name'],search_evidence='')
    def draft(self,**changes):
        values={'action':'save','revision':'0','name':'Proposed SQL','source':self.group_a,'destination':self.group_b,'protocol':'TCP','port':'1433','policy':'/infra/domains/default/security-policies/app','placement':'Before default drop','logging':'on','owner':str(self.user.pk),'justification':'Portal database access','acknowledge':'on'}
        # Form action and rule action must be separate values.
        values['rule_action']='ALLOW'
        values.update(changes);return values
    def test_pair_matches_explicit_group_and_ip_without_access_verdict(self):
        result=investigate(self.snapshot,self.src,self.dst,'TCP','1433')
        self.assertEqual(len(result['candidates']),1)
        self.assertIn('Explicit VM identity',result['source']['groups'][0]['reason'])
        self.rule.data['configuration']['rule']['source_groups']=['10.0.0.0/24'];self.rule.save()
        self.assertEqual(len(investigate(self.snapshot,self.src,self.dst,'TCP','1433')['candidates']),1)
        response=self.client.get(self.url,self.query)
        self.assertContains(response,'candidate rule')
        self.assertContains(response,'not an Allowed / Blocked verdict')
    def test_unresolved_exclusion_and_disabled_rules_are_not_silently_ignored(self):
        self.rule.data['configuration']['rule']['sources_excluded']=True;self.rule.save()
        result=investigate(self.snapshot,self.src,self.dst,'TCP','1433')
        self.assertEqual(len(result['candidates']),0);self.assertEqual(len(result['unresolved']),1)
        self.rule.data['configuration']['rule']['disabled']=True;self.rule.save()
        self.assertEqual(len(investigate(self.snapshot,self.src,self.dst)['unresolved']),0)
        self.assertEqual(len(investigate(self.snapshot,self.src,self.dst,include_disabled=True)['unresolved']),1)
    def test_nested_memberships_and_same_vm(self):
        self.row({'name':'Parent','kind':'group','path':'/groups/parent','configuration':{'expression':[{'resource_type':'PathExpression','paths':[self.group_a]}]}})
        result=investigate(self.snapshot,self.src,self.src)
        self.assertTrue(any(g['path']=='/groups/parent' for g in result['source']['groups']))
    def test_search_snapshot_isolation_and_input_validation(self):
        result=self.client.get(reverse('segmentation-vms',args=[self.env.pk]),{'snapshot':self.snapshot.pk,'q':'10.1.0.1'}).json()
        self.assertEqual(result['results'][0]['value'],'vm-db')
        other=Environment.objects.create(name='Other',slug='other-workload',manager='https://other.example')
        self.assertEqual(self.client.get(reverse('segmentation-vms',args=[other.pk]),{'snapshot':self.snapshot.pk}).status_code,404)
        response=self.client.get(self.url,dict(self.query,port='70000'))
        self.assertContains(response,'65535')
        self.assertEqual(self.client.get(self.url,{'snapshot':'not-a-uuid'}).status_code,404)
    def test_proposal_creation_edit_conflict_discard_and_export_are_local(self):
        response=self.client.post(self.draft_url,self.draft());self.assertEqual(response.status_code,302)
        obj=FirewallRuleProposal.objects.get();self.assertEqual(obj.configuration['action'],'ALLOW')
        url=reverse('segmentation-proposal',args=[self.env.pk,obj.pk])
        self.assertEqual(self.client.get(url+'?export=json').json()['deployed'],False)
        response=self.client.post(url,self.draft(revision='0'));self.assertContains(response,'Proposal changed')
        self.client.post(url,{'action':'discard','revision':'1'});obj.refresh_from_db();self.assertEqual(obj.state,'discarded')
        self.client.post(url,self.draft(revision=str(obj.revision)));obj.refresh_from_db();self.assertEqual(obj.state,'discarded')
        self.rule.refresh_from_db();self.assertEqual(self.rule.data['configuration']['rule']['action'],'ALLOW')
    def test_other_user_cannot_read_modify_or_export_draft(self):
        self.client.post(self.draft_url,self.draft());obj=FirewallRuleProposal.objects.get()
        self.client.force_login(get_user_model().objects.create_user('other'))
        url=reverse('segmentation-proposal',args=[self.env.pk,obj.pk])
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.get(url+'?export=json').status_code,404)
        self.assertEqual(self.client.post(url,{'action':'discard','revision':'1'}).status_code,404)
    def test_proposal_requires_explicit_valid_endpoints_and_reason(self):
        response=self.client.post(self.draft_url,self.draft(source='ANY',justification=''))
        self.assertEqual(response.status_code,200);self.assertFalse(FirewallRuleProposal.objects.exists())
    def test_live_check_only_reads_and_does_not_claim_negative_membership(self):
        url=reverse('segmentation-membership',args=[self.env.pk])+'?snapshot='+str(self.snapshot.pk)
        data={'vm':'vm-app','group':self.group_a}
        self.assertEqual(self.client.post(url,data).status_code,403)
        self.user.is_staff=True;self.user.save()
        class ReadOnlyClient:
            def get(self,path,params):
                assert path.endswith('/members/virtual-machines');return {'results':[],'cursor':'next'}
        @contextmanager
        def connection(env): yield ReadOnlyClient()
        with patch('inventory.deletion.connection',connection):
            result=self.client.post(url,data).json()
        self.assertFalse(result['present']);self.assertTrue(result['partial']);self.assertIn('absence is not established',result['message'])
    def test_denied_live_membership_is_reported_and_audited(self):
        from .collector import AuditError
        from .models import AuditEvent
        self.user.is_staff=True;self.user.save()
        class Denied:
            def get(self,*args): raise AuditError('Forbidden',status_code=403)
        @contextmanager
        def connection(env): yield Denied()
        with patch('inventory.deletion.connection',connection):
            response=self.client.post(reverse('segmentation-membership',args=[self.env.pk])+'?snapshot='+str(self.snapshot.pk),{'vm':'vm-app','group':self.group_a})
        self.assertEqual(response.status_code,502)
        self.assertIn('HTTP 403',response.json()['error'])
        self.assertTrue(AuditEvent.objects.filter(action='segmentation.membership_check_failed',outcome='failed').exists())
    def test_saved_pair_opens_latest_with_stable_vm_identities_and_can_be_removed(self):
        from .models import SegmentationSavedView
        from urllib.parse import urlencode
        url=self.url+'?'+urlencode(self.query)
        self.client.post(url,{'action':'save_pair','name':'App to DB'})
        view=SegmentationSavedView.objects.get()
        self.assertEqual(view.filters['source'],'vm-app');self.assertNotIn('snapshot',view.filters)
        self.client.post(url,{'action':'delete_pair','view_id':view.pk})
        self.assertFalse(SegmentationSavedView.objects.exists())
