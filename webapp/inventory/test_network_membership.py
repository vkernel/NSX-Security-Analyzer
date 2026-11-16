"""Synthetic regressions for segment groups with zero direct VM members."""
from collections import Counter
from copy import deepcopy
from unittest.mock import Mock, patch
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from . import collector
from .network_membership import collect, page_list, requires_network, _bind, enrich_vm

GP='/infra/domains/default/groups/'
SP='/infra/segments/'


def synthetic_inventory(group_count=2):
    segments=[{'path':SP+str(i),'id':'realized-segment-'+str(i),'display_name':'Segment '+str(i)} for i in range(44)]
    ports=[{'path':SP+str(i%44)+'/ports/'+str(i),'id':str(i), 'display_name':'Port '+str(i),
            'attachment':{'id':'attachment-'+str(i)}} for i in range(605)]
    vms=[{'path':'/infra/realized-state/virtual-machines/'+str(i),'external_id':'vm-'+str(i),
          'display_name':'Synthetic VM '+str(i),'tags':[]} for i in range(3)]
    vifs=[{'external_id':'vif-'+str(i),'owner_vm_id':'vm-'+str(i), 'lport_attachment_id':'attachment-'+str(i)} for i in range(2)]
    for i,vif in enumerate(vifs):
        vif['ip_address_info']=[{'ip_addresses':['192.0.2.'+str(10+i),'2001:db8::'+str(10+i)],'source':'VM_TOOLS'}]
    for vm in vms:
        vm['guest_info']={'os_name':'Synthetic guest','computer_name':vm['display_name']}
    groups=[{'path':GP+'network-'+str(i),'display_name':'Network group '+str(i), 'expression':[
        {'resource_type':'Condition','member_type':'Segment','key':'Tag','operator':'EQUALS','value':'Segment|TEST'}]} for i in range(group_count)]
    client=Mock(membership_hints={},inventory_warnings=[])
    calls=Counter()
    def get(path,params=None):
        params=params or {}; calls[path]+=1
        if path=='/api/v1/fabric/vifs': rows=vifs
        elif path.endswith('/virtual-machine-group-associations'): rows=[]
        elif '/members/' in path:
            category=path.rsplit('/',1)[-1]
            rows=segments if category in ('segments','logical-switches') else [
                {k:v for k,v in port.items() if k!='attachment'} for port in ports] if category in ('segment-ports','logical-ports') else [
                    '2001:db8::'+format(i,'x') for i in range(844)] if category=='ip-addresses' else []
        elif path.endswith('/ports'):
            rows=[port for port in ports if port['path'].rsplit('/ports/',1)[0]==path.rsplit('/ports',1)[0]]
        else: raise AssertionError('Unexpected network request: '+path)
        start=int(params.get('cursor',0)); end=start+params.get('page_size',1000)
        result={'results':deepcopy(rows[start:end]),'result_count':len(rows)}
        if end<len(rows):result['cursor']=str(end)
        return result
    client.get.side_effect=get
    client.items.side_effect=lambda path,*a,**kw:{'/infra/domains':[{'path':'/infra/domains/default'}],
        '/infra/domains/default/groups':groups,'/infra/services':[], '/infra/tags':[],
        '/infra/realized-state/virtual-machines':vms}[path]
    return client,groups,vms,vifs,ports,calls


def synthetic_report(group_count=2, reuse_search_ports=False):
    client,groups,vms,vifs,ports,calls=synthetic_inventory(group_count)
    rule={'path':'/infra/domains/default/security-policies/p/rules/r','name':'Network rule',
          'policy_name':'Network policy','policy_path':'/infra/domains/default/security-policies/p',
          'rule_id':1,'policy_rule_id':'r','source_groups':[groups[0]['path']], 'destination_groups':['ANY'],
          'scope':['ANY'],'services':['ANY'],'action':'ALLOW','disabled':False,'hit_status':'unknown','notes':[]}
    resources=groups+([dict(port,resource_type='SegmentPort') for port in ports] if reuse_search_ports else [])
    with patch.object(collector,'verified_configuration_search',return_value=(resources,{'mode':'all_types'})), \
         patch.object(collector,'audit_dfw',return_value=({'policies':[],'rules':[rule],'errors':[]},[])):
        report=collector.audit(client,workers=4)
    return report,calls


class NetworkCollectionTests(SimpleTestCase):
    def test_full_parent_lists_and_shared_ports_without_vm_group_cross_product(self):
        report,calls=synthetic_report()
        group=report['inventory']['groups'][0]
        endpoints=group['resolved_membership']['endpoints']
        for endpoint,total in [('logical-switches',44),('logical-ports',605),('ip-addresses',844)]:
            self.assertEqual(endpoints[endpoint]['status'],'complete')
            self.assertEqual(len(endpoints[endpoint]['items']),total)
            self.assertEqual(endpoints[endpoint]['reported_count'],total)
        self.assertEqual(endpoints['virtual-machines']['items'],[])
        self.assertEqual(endpoints['virtual-machines']['status'],'complete')
        self.assertEqual(len(endpoints['network-virtual-machines']['items']),2)
        self.assertEqual(calls['/api/v1/fabric/vifs'],1)
        vm=report['tags']['virtual_machines'][0]
        self.assertNotIn('ip_addresses',vm['guest_info'])
        self.assertEqual(vm['network_interfaces'][0]['ip_address_info'][0]['ip_addresses'],['192.0.2.10','2001:db8::10'])
        self.assertEqual(vm['ip_address_evidence']['status'],'complete')
        self.assertEqual(sum(count for path,count in calls.items() if path.endswith('/ports')),44)
        self.assertTrue(all(count==1 for path,count in calls.items() if path.endswith('/ports')))
        self.assertEqual(calls[GP+'network-0/members/logical-switches'],1)
        self.assertEqual(calls[GP+'network-0/members/segments'],0) # Reused full mandatory first page.
        rows=collector.vm_inventory_rows(report)
        self.assertEqual(rows[0]['direct_group_count'],0)
        self.assertEqual(rows[0]['network_group_count'],2)
        self.assertEqual(rows[0]['resolved_group_count'],2)
        self.assertEqual(rows[0]['resolved_membership_status'],'complete')
        self.assertEqual(rows[2]['resolved_group_count'],0) # Visible VM has no attached VIFs.
        self.assertEqual(rows[0]['resolved_groups'][0]['via'][0]['attachment_id'],'attachment-0')
        self.assertEqual(report['membership_evidence']['network']['status'],'complete')

    def test_nested_criteria_select_parent_reads_but_child_members_do_not_populate_parent(self):
        child={'path':GP+'child','expression':[{'resource_type':'Condition','member_type':'Segment','key':'Tag','value':'x'}]}
        parent={'path':GP+'parent','expression':[{'resource_type':'PathExpression','paths':[child['path']]}]}
        self.assertTrue(requires_network(parent,{child['path']:child,parent['path']:parent}))
        client,groups,vms,vifs,ports,calls=synthetic_inventory()
        groups=[child,parent]
        original=client.get.side_effect
        def get(path,params):
            return {'results':[],'result_count':0} if path.startswith(parent['path']+'/members/') else original(path,params)
        client.get.side_effect=get
        metadata,stats=collect(client,groups,vms,workers=4)
        self.assertEqual([edge['path'] for edge in metadata['vms'][vms[0]['path']]['items']],[child['path']])

    def test_no_network_criteria_adds_no_network_requests(self):
        client=Mock()
        group={'path':GP+'vm','expression':[{'resource_type':'Condition','member_type':'VirtualMachine','key':'Tag','value':'prod'}]}
        metadata,stats=collect(client,[group],[])
        client.get.assert_not_called()
        self.assertEqual(metadata['status'],'not_applicable')

    def test_empty_modern_ports_recover_legacy_members_once_then_share_inventory(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory(12)
        original=client.get.side_effect
        def get(path,params=None):
            if path.endswith('/members/segment-ports'):
                calls[path]+=1
                return {'results':[],'result_count':0}
            return original(path,params)
        client.get.side_effect=get
        metadata,stats=collect(client,groups,vms,resources=[dict(p,resource_type='SegmentPort') for p in ports])
        self.assertEqual(metadata['status'],'complete')
        self.assertEqual(len(metadata['vms'][vms[0]['path']]['items']),12)
        self.assertEqual(stats['empty_response_recoveries'],1)
        self.assertEqual(sum(n for p,n in calls.items() if p.endswith('/members/segment-ports')),1)
        self.assertEqual(calls['/api/v1/fabric/vifs'],1)
        self.assertEqual(stats['segment_inventories'],0)
        self.assertEqual(stats['reused_segment_inventories'],44)

    def test_empty_modern_segments_and_empty_reused_legacy_ports_are_verified(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory(1)
        original=client.get.side_effect
        def get(path,params=None):
            if path.endswith('/members/segments'):
                calls[path]+=1
                return {'results':[],'result_count':0}
            return original(path,params)
        client.get.side_effect=get
        probe=Mock(pages={'logical-ports':{'results':[],'result_count':0}},page_sizes={'logical-ports':1000})
        metadata,stats=collect(client,groups,vms,probes={groups[0]['path']:probe})
        self.assertEqual(len(metadata['groups'][groups[0]['path']]['logical-switches']['items']),44)
        self.assertEqual(len(metadata['vms'][vms[0]['path']]['items']),1)
        self.assertEqual(stats['empty_response_recoveries'],2)

    def test_recovered_positive_lists_supersede_an_earlier_empty_cleanup_probe(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory(1)
        original=client.get.side_effect
        def get(path,params=None):
            if path.endswith(('/members/logical-switches','/members/logical-ports','/members/ip-addresses')):
                calls[path]+=1
                return {'results':[],'result_count':0}
            return original(path,params)
        client.get.side_effect=get
        resources=groups+[dict(port,resource_type='SegmentPort') for port in ports]
        with patch.object(collector,'verified_configuration_search',return_value=(resources,{'mode':'all_types'})), \
             patch.object(collector,'audit_dfw',return_value=({'policies':[],'rules':[],'errors':[]},[])):
            report=collector.audit(client,workers=4)
        self.assertEqual(report['objects'][0]['membership'],'nonempty')
        self.assertEqual(report['inventory']['groups'][0]['membership'],'nonempty')
        self.assertEqual(collector.vm_inventory_rows(report)[0]['network_group_count'],1)

    def test_contradictory_empty_port_lists_remain_unknown_without_inventing_parent_edges(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory(1)
        original=client.get.side_effect
        def get(path,params=None):
            if path.endswith(('/members/segment-ports','/members/logical-ports')):
                calls[path]+=1
                return {'results':[],'result_count':0}
            return original(path,params)
        client.get.side_effect=get
        metadata,stats=collect(client,groups,vms)
        self.assertEqual(metadata['status'],'partial')
        self.assertEqual(metadata['vms'][vms[0]['path']]['status'],'partial')
        self.assertEqual(metadata['vms'][vms[0]['path']]['items'],[])
        self.assertEqual(calls[groups[0]['path']+'/members/logical-ports'],1)

    def test_empty_compatibility_permission_failure_is_partial_and_probed_once(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory(12)
        original=client.get.side_effect
        def get(path,params=None):
            if path.endswith('/members/segment-ports'):
                calls[path]+=1
                return {'results':[],'result_count':0}
            if path.endswith('/members/logical-ports'):
                calls[path]+=1
                raise collector.AuditError('denied',status_code=403)
            return original(path,params)
        client.get.side_effect=get
        metadata,stats=collect(client,groups,vms)
        self.assertEqual(metadata['status'],'partial')
        self.assertEqual(sum(n for p,n in calls.items() if p.endswith('/members/logical-ports')),1)
        self.assertTrue(all(e['logical-ports']['status']=='partial' for e in metadata['groups'].values()))

    def test_testing_samples_one_network_group_and_never_expands_all_groups(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory(50)
        metadata,stats=collect(client,groups,vms[:1],testing=True)
        self.assertEqual(stats['groups'],1)
        self.assertLessEqual(stats['requests'],6)
        self.assertEqual(metadata['status'],'partial')
        self.assertEqual(len(metadata['groups']),1)
        self.assertTrue(all(len(result['items'])==1 for result in metadata['groups'][groups[0]['path']].values()))

    def test_existing_port_configuration_is_reused_and_fabric_ids_are_bridged_once(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory()
        original=client.get.side_effect
        def get(path,params):
            if path=='/api/v1/fabric/vifs':
                calls[path]+=1
                return {'results':[dict(vif,owner_vm_id='fabric-'+str(i)) for i,vif in enumerate(vifs)],'result_count':len(vifs)}
            if path=='/api/v1/fabric/virtual-machines':
                calls[path]+=1
                return {'results':[dict(vm,id='fabric-'+str(i)) for i,vm in enumerate(vms)],'result_count':len(vms)}
            return original(path,params)
        client.get.side_effect=get
        metadata,stats=collect(client,groups,vms,resources=[dict(port,resource_type='SegmentPort') for port in ports],collect_addresses=True)
        self.assertEqual(calls['/api/v1/fabric/vifs'],1)
        self.assertEqual(calls['/api/v1/fabric/virtual-machines'],1)
        self.assertEqual(sum(count for path,count in calls.items() if path.endswith('/ports')),0)
        self.assertEqual(stats['reused_segment_inventories'],44)
        self.assertEqual(metadata['status'],'complete')
        self.assertEqual(len(metadata['vms'][vms[0]['path']]['items']),2)
        self.assertEqual(vms[0]['network_interfaces'][0]['ip_address_info'][0]['ip_addresses'][0],'192.0.2.10')

    def test_partial_pages_keep_positives_and_do_not_claim_complete(self):
        client=Mock()
        client.get.side_effect=[{'results':[{'path':SP+'one','id':'realized-one'}],'result_count':44,'cursor':'next'},
                                collector.AuditError('sensitive payload',status_code=503)]
        result=page_list(client,GP+'a/members/segments',endpoint='logical-switches')
        self.assertEqual(result['status'],'partial');self.assertEqual(result['reported_count'],44)
        self.assertEqual(len(result['items']),1);self.assertNotIn('sensitive',str(result))
        client.get.side_effect=[{'results':[{'id':'a'}],'result_count':2,'cursor':'same'},
                                {'results':[{'id':'a'}],'result_count':2,'cursor':'same'}]
        self.assertEqual(page_list(client,'/members',endpoint='logical-switches')['status'],'partial')
        from .request_pacing import CollectionDeadlineExceeded
        client.get.side_effect=CollectionDeadlineExceeded('expired')
        with self.assertRaises(CollectionDeadlineExceeded):page_list(client,'/members')

    def test_malformed_mandatory_probe_is_not_reused_as_a_network_list(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory(1)
        probe=Mock(pages={'logical-ports':{'results':None}},page_sizes={'logical-ports':1000})
        metadata,stats=collect(client,groups,vms,probes={groups[0]['path']:probe})
        self.assertEqual(metadata['status'],'complete')
        self.assertEqual(calls[groups[0]['path']+'/members/segment-ports'],1)

    def test_compatibility_permissions_and_failures_stay_explicit(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory()
        original=client.get.side_effect
        def get(path,params):
            if path.endswith('/members/segment-ports') or path.endswith('/members/segments'):
                calls[path]+=1;raise collector.AuditError('unsupported',status_code=404)
            if path=='/api/v1/fabric/vifs':raise collector.AuditError('denied',status_code=403)
            return original(path,params)
        client.get.side_effect=get
        metadata,stats=collect(client,groups,vms,workers=4)
        self.assertEqual(metadata['status'],'partial')
        self.assertTrue(all(v['status']=='partial' and not v['items'] for v in metadata['vms'].values()))
        self.assertEqual(sum(c for p,c in calls.items() if p.endswith('/members/segment-ports')),1)

    def test_attachment_ambiguity_and_name_or_ip_matching_never_create_edges(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory(1)
        metadata,_=collect(client,groups,vms)
        conflicting=[vifs[0],dict(vifs[0],owner_vm_id='vm-1')]
        _bind(metadata,vms,conflicting,ports)
        self.assertTrue(all(not v['items'] for v in metadata['vms'].values()))
        self.assertEqual(metadata['status'],'partial')
        _bind(metadata,vms,[dict(vifs[0],owner_vm_id=vms[0]['display_name'])],ports)
        self.assertTrue(all(not v['items'] for v in metadata['vms'].values()))
        _bind(metadata,vms,[vifs[0]],[ports[0],dict(ports[1],attachment=ports[0]['attachment'])])
        self.assertFalse(metadata['vms'][vms[0]['path']]['items'])
        _bind(metadata,vms,[vifs[0],dict(vifs[0],lport_attachment_id='attachment-1')],ports)
        self.assertFalse(metadata['vms'][vms[0]['path']]['items'])

    def test_direct_and_network_membership_are_deduplicated_with_both_provenances(self):
        row={'path':'vm','resolved_groups':[{'path':GP+'a','name':'A'}], 'resolved_group_count':1,
             'resolved_membership_status':'complete'}
        metadata={'version':1,'status':'complete','vms':{'vm':{'status':'complete','items':[
            {'path':GP+'a','name':'A','membership_basis':'network_attachment','via':[{'interface_id':'vif'}]}]}}}
        enrich_vm(row,metadata)
        self.assertEqual(row['resolved_group_count'],1)
        self.assertEqual(row['direct_group_count'],1);self.assertEqual(row['network_group_count'],1)
        self.assertEqual(row['resolved_groups'][0]['membership_basis'],'direct_and_network')

    def test_management_inventory_uses_the_same_manager_not_the_policy_prefix(self):
        client=collector.NSXClient('https://synthetic.invalid','synthetic','synthetic')
        client.http=Mock();client.http.request.return_value=Mock(status=200,data=b'{"results": []}')
        client.get('/api/v1/fabric/vifs',{'page_size':1000})
        self.assertEqual(client.http.request.call_args.args[1], 'https://synthetic.invalid/api/v1/fabric/vifs?page_size=1000')


class NetworkSnapshotTests(TestCase):
    def test_recovered_empty_port_response_reaches_vm_counts_members_and_firewall(self):
        from .models import Environment,Snapshot,SnapshotRecord
        from .snapshot_index import build
        from .vm_relationships import read as vm_read
        from .group_memberships import read as group_read
        good,_=synthetic_report(1,reuse_search_ports=True)
        client,groups,vms,vifs,ports,calls=synthetic_inventory(1)
        original=client.get.side_effect
        def get(path,params=None):
            if path.endswith('/members/segment-ports'):
                calls[path]+=1
                return {'results':[],'result_count':0}
            return original(path,params)
        client.get.side_effect=get
        resources=groups+[dict(port,resource_type='SegmentPort') for port in ports]
        with patch.object(collector,'verified_configuration_search',return_value=(resources,{'mode':'all_types'})), \
             patch.object(collector,'audit_dfw',return_value=(deepcopy(good['dfw']),[])):
            report=collector.audit(client,workers=4)
        env=Environment.objects.create(name='Recovered network',slug='recovered',manager='https://recovered.invalid')
        snapshot=Snapshot.objects.create(environment=env,generated_at=timezone.now(),report=report)
        with patch('inventory.deletion.connection',side_effect=AssertionError('No live NSX for saved browsing')):
            build(snapshot)
            vm=SnapshotRecord.objects.get(snapshot=snapshot,view='vms',name='Synthetic VM 0')
            self.assertEqual(vm.compact['resolved_group_count'],1)
            self.assertEqual(len(group_read(snapshot.pk,GP+'network-0','network-virtual-machines')['items']),2)
            self.assertEqual(len(vm_read(snapshot.pk,vm.ordinal,'confirmed_rules')['items']),1)
        self.assertEqual(report['membership_evidence']['network']['diagnostics']['empty_response_recoveries'],1)

    def test_current_firewall_keeps_saved_network_links_and_inspection_with_separate_timestamp(self):
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.urls import reverse
        from .models import Environment,Snapshot,SnapshotRecord,MembershipRelation
        from .snapshot_index import build
        from .current_membership import subject,save
        from .confirmed_references import saved
        report,_=synthetic_report(1,reuse_search_ports=True)
        env=Environment.objects.create(name='Mixed references',slug='mixed',manager='https://mixed.invalid')
        snapshot=Snapshot.objects.create(environment=env,generated_at=timezone.now(),report=report)
        build(snapshot)
        vm=SnapshotRecord.objects.get(snapshot=snapshot,view='vms',name='Synthetic VM 0')
        owner=subject(snapshot,'vm',vm.ordinal)
        save(env,'vm',owner,[],'complete',[])
        browser=Client();browser.force_login(get_user_model().objects.create_user('mixed-operator',is_staff=True))
        with patch('inventory.deletion.connection',side_effect=AssertionError('Cached browsing must not contact NSX')):
            result=browser.get(reverse('current-membership',args=[snapshot.pk]),
                {'kind':'vm','id':vm.ordinal,'section':'firewall'}).json()
            self.assertEqual(len(result['items']),1)
            self.assertTrue(result['has_saved_network'])
            self.assertEqual(result['source'],'current_with_saved_network')
            self.assertEqual(result['network_checked_at'],vm.compact['network_membership_checked_at'])
            self.assertEqual(result['items'][0]['network_checked_at'],result['network_checked_at'])
            self.assertIn('Attachment evidence is saved',result['items'][0]['reference_reason'])
            detail=browser.get(reverse('snapshot-data',args=[snapshot.pk]),
                {'op':'current-member-rule','id':vm.ordinal,'path':result['items'][0]['path']})
            self.assertEqual(detail.status_code,200)
            evidence=detail.json()['details']['membership_evidence'][GP+'network-0']
            self.assertEqual(evidence['membership_source'],'saved_snapshot')
            self.assertEqual(evidence['membership_basis'],'network_attachment')
            self.assertEqual(len(saved(snapshot.pk,vm.ordinal,'confirmed_rules')['items']),1)
        self.assertEqual(MembershipRelation.objects.count(),0) # Saved edges never become current observations.
        snapshot.refresh_from_db();self.assertEqual(snapshot.report,report)

    def test_saved_vm_counts_group_categories_and_firewall_references_agree_without_nsx(self):
        from .models import Environment,Snapshot,SnapshotRecord
        from .snapshot_index import build
        from .vm_relationships import read as vm_read
        from .group_memberships import read as group_read
        report,calls=synthetic_report()
        env=Environment.objects.create(name='Synthetic network',slug='synthetic-network',manager='https://synthetic.invalid')
        snapshot=Snapshot.objects.create(environment=env,generated_at=timezone.now(),report=report)
        with patch('inventory.deletion.connection',side_effect=AssertionError('Saved browsing must not contact NSX')):
            build(snapshot)
            vm=SnapshotRecord.objects.get(snapshot=snapshot,view='vms',name='Synthetic VM 0')
            self.assertEqual(vm.compact['resolved_group_count'],2)
            self.assertEqual(vm.compact['direct_group_count'],0)
            links=vm_read(snapshot.pk,vm.ordinal,'network_groups')
            self.assertEqual(links['status'],'complete');self.assertEqual(len(links['items']),2)
            rules=vm_read(snapshot.pk,vm.ordinal,'confirmed_rules')
            self.assertEqual(len(rules['items']),1)
            self.assertIn('attachment',rules['items'][0]['reference_reason'])
            self.assertEqual(len(group_read(snapshot.pk,GP+'network-0','network-virtual-machines')['items']),2)
            self.assertEqual(group_read(snapshot.pk,GP+'network-0','virtual-machines')['saved_count'],0)
            for section,total in [('logical-switches',44),('logical-ports',605),('ip-addresses',844)]:
                result=group_read(snapshot.pk,GP+'network-0',section)
                self.assertEqual(result['saved_count'],total);self.assertEqual(result['status'],'complete')
