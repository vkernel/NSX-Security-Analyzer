"""Use the documented fabric VIF schema, not fabricated IP fields on GuestInfo."""
from copy import deepcopy
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import collector, ip_references
from .models import Environment, Snapshot, SnapshotRecord, SnapshotIPReferenceIndex
from .network_membership import collect
from .test_network_membership import synthetic_inventory, GP
from .vm_addresses import save


def collected_ip_report():
    client,groups,vms,vifs,ports,calls=synthetic_inventory(1)
    groups[0]['expression']=[{'resource_type':'IPAddressExpression','ip_addresses':['192.0.2.0/24']}]
    rule={'path':'/infra/domains/default/security-policies/p/rules/subnet', 'name':'Subnet access',
          'policy_name':'Policy','policy_path':'/infra/domains/default/security-policies/p',
          'rule_id':1,'policy_rule_id':'subnet','source_groups':[groups[0]['path']],
          'destination_groups':['ANY'],'scope':['ANY'],'services':['ANY'],
          'action':'ALLOW','disabled':False,'hit_status':'unknown','notes':[]}
    with patch.object(collector,'verified_configuration_search',return_value=(groups,{'mode':'all_types'})), \
         patch.object(collector,'audit_dfw',return_value=({'policies':[],'rules':[rule],'errors':[]},[])):
        report=collector.audit(client,workers=4)
    return report,calls


class VMAddressCollectionTests(SimpleTestCase):
    def test_ip_only_groups_capture_vifs_once_without_segment_or_per_vm_reads(self):
        report,calls=collected_ip_report()
        rows=collector.vm_inventory_rows(report)
        self.assertEqual(calls['/api/v1/fabric/vifs'],1)
        self.assertEqual(calls['/api/v1/fabric/virtual-machines'],0)
        self.assertEqual(sum(n for p,n in calls.items() if p.endswith('/ports')),0)
        self.assertEqual(report['membership_evidence']['network']['status'],'not_applicable')
        self.assertEqual(ip_references.addresses(rows[0]['vm_details'])[0],['192.0.2.10','2001:db8::10'])
        self.assertEqual(rows[0]['resolved_group_count'],0)
        self.assertEqual(report['performance']['membership']['network']['vm_addresses']['per_vm_requests'],0)

    def test_partial_vif_pagination_retains_addresses_with_partial_coverage(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory(0)
        client.get.side_effect=[{'results':[vifs[0]],'result_count':2,'cursor':'next'},
                                collector.AuditError('synthetic failure',status_code=503)]
        metadata,stats=collect(client,groups,vms,collect_addresses=True)
        self.assertEqual(client.get.call_count,2)
        self.assertEqual(vms[0]['ip_address_evidence']['status'],'partial')
        self.assertEqual(ip_references.addresses(vms[0])[0],['192.0.2.10','2001:db8::10'])
        self.assertEqual(vms[1]['network_interfaces'],[])

    def test_conflicting_vif_and_vm_identities_never_leak_addresses(self):
        _,_,vms,vifs,_,_=synthetic_inventory(0)
        aliases={'vm-0':{vms[0]['path']},'vm-1':{vms[1]['path']}}
        conflict=dict(deepcopy(vifs[0]),owner_vm_id='vm-1')
        save(vms,[vifs[0],conflict],aliases,complete=True,checked_at='2026-11-11T10:00:00Z')
        self.assertTrue(all(not vm['network_interfaces'] for vm in vms))
        aliases['vm-0']={vms[0]['path'],vms[1]['path']}
        save(vms,[vifs[0]],aliases,complete=True,checked_at='2026-11-11T10:00:00Z')
        self.assertTrue(all(not vm['network_interfaces'] for vm in vms))

    def test_deleted_and_malformed_interfaces_are_not_address_evidence(self):
        _,_,vms,vifs,_,_=synthetic_inventory(0)
        aliases={'vm-0':{vms[0]['path']}}
        bad=dict(vifs[0],ip_address_info='invalid')
        save(vms,[dict(vifs[0],marked_for_delete=True),bad,dict(vifs[1],owner_vm_id=[])],
             aliases,complete=True,checked_at='2026-11-11T10:00:00Z')
        self.assertTrue(all(not vm['network_interfaces'] for vm in vms))
        self.assertEqual(vms[0]['ip_address_evidence']['status'],'partial')

    def test_conflicting_vif_pages_do_not_transfer_an_address_to_another_vm(self):
        client,groups,vms,vifs,ports,calls=synthetic_inventory(0)
        client.get.side_effect=[{'results':[vifs[0]],'result_count':2,'cursor':'next'},
            {'results':[dict(deepcopy(vifs[0]),owner_vm_id='vm-1')],'result_count':2}]
        metadata,stats=collect(client,groups,vms,collect_addresses=True)
        self.assertTrue(all(not vm['network_interfaces'] for vm in vms))
        self.assertEqual(vms[1]['ip_address_evidence']['status'],'partial')

    def test_large_vm_inventory_uses_one_shared_vif_page_without_membership_fanout(self):
        client,_,_,_,_,_=synthetic_inventory(0)
        vms=[{'path':f'/infra/realized-state/virtual-machines/{i}','external_id':f'vm-{i}'} for i in range(3000)]
        vifs=[{'external_id':f'vif-{i}','owner_vm_id':f'vm-{i}',
               'ip_address_info':[{'source':'VM_TOOLS','ip_addresses':[f'10.0.{i//256}.{i%256}']}]} for i in range(3000)]
        def get(path,params):
            self.assertEqual(path,'/api/v1/fabric/vifs')
            start=int(params.get('cursor',0));end=start+1000
            return {'results':vifs[start:end],'result_count':3000,**({'cursor':str(end)} if end<3000 else {})}
        client.get.side_effect=get
        metadata,stats=collect(client,[],vms,collect_addresses=True)
        self.assertEqual(client.get.call_count,3)
        self.assertEqual(metadata['address_diagnostics']['vms_with_reported_addresses'],3000)
        self.assertEqual(vms[-1]['ip_address_evidence']['status'],'complete')


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SavedVIFAddressFlowTests(TestCase):
    def test_realistic_collection_to_snapshot_and_drawer_retains_ip_firewall_refs(self):
        from .snapshot_index import build
        report,calls=collected_ip_report()
        env=Environment.objects.create(name='Synthetic VIF IPs',slug='vif-ips',manager='https://vif-ips.invalid')
        snapshot=Snapshot.objects.create(environment=env,generated_at=timezone.now(),report=report)
        build(snapshot)
        vm=SnapshotRecord.objects.get(snapshot=snapshot,view='vms',name='Synthetic VM 0')
        self.assertNotIn('ip_addresses',vm.data['vm_details']['guest_info'])
        self.assertEqual(vm.compact['resolved_group_count'],0)
        user=get_user_model().objects.create_user('vif-ip-review',is_staff=True)
        self.client.force_login(user)
        with patch('inventory.deletion.connection',side_effect=AssertionError('Saved browsing must not call NSX')):
            url=reverse('snapshot-data',args=[snapshot.pk])
            matches=self.client.get(url,{'op':'vm-relationships','id':vm.ordinal,'section':'ip_groups'}).json()
            self.assertEqual(matches['status'],'complete')
            self.assertEqual(matches['items'][0]['path'],GP+'network-0')
            self.assertEqual(matches['items'][0]['ip_matches'][0]['ip'],'192.0.2.10')
            rules=self.client.get(url,{'op':'vm-relationships','id':vm.ordinal,'section':'confirmed_rules'}).json()
            self.assertEqual(rules['items'][0]['name'],'Subnet access')
            self.assertEqual(rules['items'][0]['reference_basis'],'ip_subnet')
        self.assertEqual(calls['/api/v1/fabric/vifs'],1)
        self.assertTrue(SnapshotIPReferenceIndex.objects.filter(snapshot=snapshot,version=ip_references.VERSION).exists())

    def test_collected_but_empty_and_incomplete_addresses_remain_distinct(self):
        from .test_ip_references import VP
        env=Environment.objects.create(name='No VIF IPs',slug='empty-vif-ips',manager='https://empty-vif-ips.invalid')
        snapshot=Snapshot.objects.create(environment=env,generated_at=timezone.now(),report={})
        SnapshotRecord.objects.create(snapshot=snapshot,view='inventory',ordinal=0,name='Subnet',compact={},columns=[],sort_values={},
            data={'kind':'group','path':GP+'subnet','configuration':{'expression':[{'resource_type':'IPAddressExpression','ip_addresses':['192.0.2.0/24']}]}})
        vm=SnapshotRecord.objects.create(snapshot=snapshot,view='vms',ordinal=1,name='VM',compact={'path':VP},columns=[],sort_values={},
            data={'vm_details':{'network_interfaces':[],'ip_address_evidence':{'version':1,'status':'complete','interface_count':1}}})
        result=ip_references.read(snapshot.pk,vm.ordinal)
        self.assertEqual(result['status'],'unavailable')
        self.assertIn('NSX returned no usable addresses',' '.join(result['notes']))
        vm.data['vm_details']['network_interfaces']=[{'ip_address_info':[{'source':'VM_TOOLS','ip_addresses':['192.0.2.10']}]}]
        vm.data['vm_details']['ip_address_evidence']['status']='partial';vm.save()
        result=ip_references.read(snapshot.pk,vm.ordinal)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(len(result['items']),1)
