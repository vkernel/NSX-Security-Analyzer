"""Synthetic saved-IP references do not become confirmed/live VM membership."""
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,SimpleTestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from django.http import Http404
from .models import (Environment,Snapshot,SnapshotRecord,SnapshotIPReferenceIndex,
                     SnapshotIPReferenceRange,SnapshotVMAddress,MembershipRelation)
from . import ip_references
from .confirmed_references import saved

GP='/infra/domains/default/groups/'
VP='/infra/realized-state/virtual-machines/a'


class IPParsingTests(SimpleTestCase):
    def test_only_runtime_address_fields_and_portable_unicast_addresses_are_used(self):
        ips,notes=ip_references.addresses({'display_name':'203.0.113.1','tags':[{'ip_address':'203.0.113.2'}],
            'guest_info':{'ip_addresses':['192.0.2.10/24','2001:db8::10','fe80::1%eth0','127.0.0.1','224.0.0.1','0.0.0.0','bad'],
                          'gateway':{'ip_address':'192.0.2.1'},'dns_servers':['192.0.2.53']}})
        self.assertEqual(ips,['192.0.2.10','2001:db8::10']);self.assertTrue(notes)

    def test_canonical_hosts_subnets_ranges_and_invalid_entries(self):
        self.assertEqual(ip_references.interval('192.0.2.10/24')[-1],'192.0.2.0/24')
        self.assertEqual(ip_references.interval('192.0.2.10')[-1],'192.0.2.10')
        self.assertEqual(ip_references.interval('2001:db8::1-2001:db8::a')[-1],'2001:db8::1-2001:db8::a')
        for value in ('ANY','192.0.2.20-192.0.2.1','192.0.2.1-2001:db8::1','fe80::1%eth0',None):
            with self.subTest(value=value),self.assertRaises((ValueError,TypeError)):ip_references.interval(value)


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class IPReferenceTests(TestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='Synthetic IP references',slug='ip-refs',manager='https://ip-refs.invalid')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={},summary={})
        self.ordinal=0
        self.vm=self.record('vms','VM',{'path':VP,'vm_details':{'external_id':'a','guest_info':{'ip_addresses':['192.0.2.10']}},'resolved_groups':[]},
            {'path':VP,'resolved_group_count':0,'resolved_membership_status':'complete','network_membership_version':1,'network_membership_status':'not_applicable'})
        self.user=get_user_model().objects.create_user('ip-refs-operator',is_staff=True)
        self.client.force_login(self.user)

    def record(self,view,name,data,compact=None):
        self.ordinal+=1
        return SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=self.ordinal,view=view,name=name,
            compact=compact or {'path':data.get('path')},data=data,columns=[],sort_values={})

    def group(self,name='Subnet',entries=None,config=None,path=None):
        path=path or GP+name
        return self.record('inventory',name,{'path':path,'kind':'group','configuration':config or {
            'expression':[{'resource_type':'IPAddressExpression','ip_addresses':entries if entries is not None else ['192.0.2.0/24']}]}})

    def rule(self,name='Rule',groups=None,excluded=False,destination=None):
        path='/infra/domains/default/security-policies/p/rules/'+name
        groups=groups if groups is not None else [GP+'Subnet']
        rule={'source_groups':groups,'destination_groups':destination or ['ANY'],'scope':['ANY'],'sources_excluded':excluded}
        return self.record('dfw',name,{'name':name,'path':path,'policy_name':'Policy','action':'ALLOW','disabled':False,
            **rule,'services':['ANY'],'configuration':{'rule':rule}})

    def context(self):return ip_references.context(self.snapshot.pk,self.vm.ordinal)

    def test_subnet_reference_is_saved_evidence_without_changing_vm_group_counts(self):
        self.group();rule=self.rule()
        with patch('inventory.deletion.connection',side_effect=AssertionError('Saved reads must not contact NSX')):
            result=saved(self.snapshot.pk,self.vm.ordinal,'confirmed_rules')
            self.assertEqual(len(result['items']),1)
            item=result['items'][0]
            self.assertEqual(item['reference_basis'],'ip_subnet')
            self.assertIn('not confirmed VM membership',item['reference_reason'])
            self.assertEqual(item['ip_matches'][0]['ip'],'192.0.2.10')
            self.assertEqual(item['ip_matches'][0]['entry'],'192.0.2.0/24')
            self.assertEqual(item['matched_fields'],['Source'])
            detail=saved(self.snapshot.pk,self.vm.ordinal,'confirmed_rule',path=rule.data['path'])['details']
            self.assertEqual(detail['membership_evidence'][GP+'Subnet']['reference_basis'],'ip_subnet')
        self.vm.refresh_from_db();self.assertEqual(self.vm.compact['resolved_group_count'],0)
        self.assertEqual(self.vm.data['resolved_groups'],[]);self.assertFalse(MembershipRelation.objects.exists())

    def test_hosts_ranges_boundaries_and_ipv6_precision(self):
        self.vm.data['vm_details']['guest_info']['ip_addresses']=['192.0.2.10','2001:db8:ffff:ffff:ffff:ffff:ffff:fffe']
        self.vm.save()
        self.group('Host',['192.0.2.10']);self.group('Range',['192.0.2.10-192.0.2.20'])
        self.group('Outside',['192.0.2.11/32']);self.group('V6',['2001:db8:ffff:ffff::/64'])
        groups,metadata=self.context()
        self.assertEqual(set(groups),{GP+'Host',GP+'Range',GP+'V6'})
        self.assertEqual(metadata['ip_status'],'complete')
        self.assertEqual(groups[GP+'V6']['ip_matches'][0]['ip'],'2001:db8:ffff:ffff:ffff:ffff:ffff:fffe')

    def test_pure_nested_ip_unions_work_but_mixed_conditions_cycles_and_missing_children_do_not(self):
        self.group('Leaf')
        self.group('Parent',config={'expression':[{'resource_type':'PathExpression','paths':[GP+'Leaf']}]})
        self.group('Mixed',config={'expression':[{'resource_type':'IPAddressExpression','ip_addresses':['192.0.2.0/24']},
            {'resource_type':'ConjunctionOperator','conjunction_operator':'AND'},
            {'resource_type':'Condition','member_type':'VirtualMachine','key':'Tag','operator':'EQUALS','value':'app|x'}]})
        self.group('Cycle',config={'expression':[{'resource_type':'PathExpression','paths':[GP+'Cycle']}]})
        self.group('Missing',config={'expression':[{'resource_type':'PathExpression','paths':[GP+'Absent']}]})
        self.rule(groups=[GP+'Parent',GP+'Mixed'])
        groups,metadata=self.context()
        self.assertEqual(set(groups),{GP+'Leaf',GP+'Parent'})
        self.assertEqual(groups[GP+'Parent']['ip_matches'][0]['via_groups'],[GP+'Leaf'])
        self.assertEqual(metadata['ip_status'],'partial')
        item=saved(self.snapshot.pk,self.vm.ordinal,'confirmed_rules')['items'][0]
        self.assertEqual(item['via_groups'],[GP+'Parent'])

    def test_rule_exclusions_are_labelled_and_rules_matching_both_fields_are_deduplicated(self):
        self.group();self.rule(excluded=True,destination=[GP+'Subnet'])
        item=saved(self.snapshot.pk,self.vm.ordinal,'confirmed_rules')['items'][0]
        self.assertEqual(item['matched_fields'],['Source (excluded)','Destination'])
        self.assertTrue(item['exclusion_reference'])
        self.assertIn('not positive endpoint matches',item['reference_reason'])

    def test_shared_addresses_are_ambiguous_and_project_scopes_do_not_cross_match(self):
        self.group()
        self.record('vms','Other VM',{'path':'/infra/realized-state/virtual-machines/b','vm_details':{
            'external_id':'b','guest_info':{'ip_addresses':['192.0.2.10']}}}, {'path':'/infra/realized-state/virtual-machines/b'})
        groups,metadata=self.context()
        self.assertEqual(groups,{})
        self.assertEqual(metadata['ip_status'],'partial');self.assertIn('Shared VM addresses',' '.join(metadata['ip_notes']))
        self.group('Project',['192.0.2.0/24'],path='/orgs/o/projects/p/infra/domains/default/groups/Project')
        other=self.record('vms','Project VM',{'path':'/orgs/o/projects/p/infra/realized-state/virtual-machines/p',
            'vm_details':{'guest_info':{'ip_addresses':['192.0.2.10']}}},{'path':'/orgs/o/projects/p/infra/realized-state/virtual-machines/p'})
        groups,metadata=ip_references.context(self.snapshot.pk,other.ordinal)
        self.assertEqual(set(groups),{'/orgs/o/projects/p/infra/domains/default/groups/Project'})

    def test_confirmed_membership_and_ip_evidence_are_deduplicated(self):
        self.group();self.rule()
        self.vm.data['resolved_groups']=[{'path':GP+'Subnet','name':'Subnet'}];self.vm.save()
        result=saved(self.snapshot.pk,self.vm.ordinal,'confirmed_rules')
        self.assertEqual(len(result['items']),1)
        self.assertEqual(result['items'][0]['reference_basis'],'confirmed_and_ip')

    def test_ip_groups_and_firewall_rules_are_paged_without_vm_group_collection_calls(self):
        for i in range(30):self.group(f'Group-{i:02}');self.rule(f'Rule-{i:02}',groups=[GP+f'Group-{i:02}'])
        with patch('inventory.deletion.connection',side_effect=AssertionError('No NSX calls')):
            first=ip_references.read(self.snapshot.pk,self.vm.ordinal)
            second=ip_references.read(self.snapshot.pk,self.vm.ordinal,1)
            self.assertEqual(len(first['items']),25);self.assertTrue(first['has_next'])
            self.assertEqual(len(second['items']),5);self.assertFalse(second['has_next'])
            rules=saved(self.snapshot.pk,self.vm.ordinal,'confirmed_rules')
            self.assertEqual(len(rules['items']),25);self.assertTrue(rules['has_next'])
            self.assertEqual(len(saved(self.snapshot.pk,self.vm.ordinal,'confirmed_rules',1)['items']),5)

    def test_index_is_reused_and_invalidated_when_saved_configuration_changes(self):
        group=self.group();self.context()
        with patch('inventory.ip_references.compile_groups',side_effect=AssertionError('Warm browse scanned all groups')):
            self.assertIn(GP+'Subnet',self.context()[0])
        group.data['configuration']['expression'][0]['ip_addresses']=['198.51.100.0/24'];group.save()
        self.assertFalse(SnapshotIPReferenceIndex.objects.exists())
        self.assertEqual(self.context()[0],{})
        self.snapshot.browsing_status='archived';self.snapshot.save(update_fields=['browsing_status'])
        self.assertFalse(SnapshotIPReferenceIndex.objects.exists())
        with self.assertRaises(Http404):self.context()
        self.assertFalse(SnapshotIPReferenceRange.objects.exists());self.assertFalse(SnapshotVMAddress.objects.exists())

    def test_current_mode_and_rule_inspection_retain_explicitly_saved_ip_provenance(self):
        from .current_membership import subject,save
        self.group();rule=self.rule()
        save(self.env,'vm',subject(self.snapshot,'vm',self.vm.ordinal),[],'complete',[])
        with patch('inventory.deletion.connection',side_effect=AssertionError('Cached browsing is not a live IP check')):
            result=self.client.get(reverse('current-membership',args=[self.snapshot.pk]),
                {'kind':'vm','id':self.vm.ordinal,'section':'firewall'}).json()
            self.assertEqual(result['source'],'current_with_saved_references')
            self.assertEqual(result['items'][0]['reference_basis'],'ip_subnet')
            response=self.client.get(reverse('snapshot-data',args=[self.snapshot.pk]),
                {'op':'current-member-rule','id':self.vm.ordinal,'path':rule.data['path']})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['details']['membership_evidence'][GP+'Subnet']['ip_matches'][0]['ip'],'192.0.2.10')

    def test_missing_addresses_and_malformed_definitions_remain_unknown(self):
        self.group();self.vm.data['vm_details']={'external_id':'a'};self.vm.save()
        self.assertEqual(self.context()[1]['ip_status'],'not_collected')
        result=saved(self.snapshot.pk,self.vm.ordinal,'confirmed_rules')
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['items'],[])
        self.group('Malformed',['192.0.2.10','invalid'])
        self.vm.data['vm_details']['guest_info']={'ip_addresses':['192.0.2.10']};self.vm.save()
        groups,metadata=self.context()
        self.assertNotIn(GP+'Malformed',groups);self.assertEqual(metadata['ip_status'],'partial')

    def test_collection_warnings_and_test_samples_preserve_positive_matches_as_partial(self):
        self.group()
        for field,value in [('summary',{'inventory_warnings':['Synthetic warning']}),('testing',True)]:
            setattr(self.snapshot,field,value);self.snapshot.save(update_fields=[field])
            groups,metadata=self.context()
            self.assertIn(GP+'Subnet',groups)
            self.assertEqual(metadata['ip_status'],'partial')

    def test_address_and_range_limits_are_explicit_and_snapshot_isolation_is_preserved(self):
        self.group()
        self.vm.data['vm_details']['guest_info']['ip_addresses']=['192.0.2.10','192.0.2.11'];self.vm.save()
        with patch('inventory.ip_references.MAX_ADDRESSES',1):
            groups,metadata=self.context()
        self.assertIn(GP+'Subnet',groups);self.assertEqual(metadata['ip_status'],'partial')
        self.assertIn('excess VM addresses',' '.join(metadata['ip_notes']))
        other=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={},summary={})
        row=SnapshotRecord.objects.create(snapshot=other,ordinal=1,view='vms',name='Other snapshot',columns=[],sort_values={},
            compact={'path':VP},data={'vm_details':{'guest_info':{'ip_addresses':['192.0.2.10']}}})
        self.assertEqual(ip_references.context(other.pk,row.ordinal)[0],{})
        SnapshotIPReferenceIndex.objects.filter(snapshot=self.snapshot).delete()
        with patch('inventory.ip_references.MAX_RANGES',0):
            groups,metadata=self.context()
        self.assertEqual(groups,{})
        self.assertEqual(metadata['ip_status'],'partial')

    def test_non_ip_network_paths_are_outside_scope_and_dense_evidence_is_bounded(self):
        self.group('Segment',config={'expression':[{'resource_type':'PathExpression','paths':['/infra/segments/s']}]})
        self.group('Dense',[f'192.0.2.{i}-192.0.2.100' for i in range(1,10)])
        with patch('inventory.ip_references.MAX_EVIDENCE',2):
            groups,metadata=self.context()
        self.assertEqual(metadata['ip_status'],'complete')
        self.assertNotIn(GP+'Segment',groups)
        self.assertEqual(len(groups[GP+'Dense']['ip_matches']),2)
        self.assertTrue(groups[GP+'Dense']['ip_matches_truncated'])
        self.assertEqual(groups[GP+'Dense']['ip_match_count'],9)

    def test_scope_only_changes_invalidate_the_address_cache(self):
        self.group();self.context()
        self.vm.compact['path']='/orgs/o/projects/p/infra/realized-state/virtual-machines/a'
        self.vm.save(update_fields=['compact'])
        self.assertFalse(SnapshotIPReferenceIndex.objects.exists())
        self.assertEqual(self.context()[0],{})
