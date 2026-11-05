"""Synthetic collection regression: four actual groups, 25 unrelated tag candidates."""
from unittest.mock import Mock, patch
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from . import collector
from .vm_associations import read, collect, apply_to_groups

GP = '/infra/domains/default/groups/'
VM = {'path': '/infra/realized-state/virtual-machines/a', 'external_id': 'a',
      'display_name': 'Synthetic VM', 'tags': [{'scope': 'env', 'tag': 'prod'}]}


class AssociationReadsTests(SimpleTestCase):
    def test_exact_external_identity_and_all_pages_not_tag_or_name_matches(self):
        client = Mock()
        client.get.side_effect = [
            {'results': [{'path': GP+'a', 'target_display_name': 'A', 'is_valid': 'true'}], 'result_count': 2, 'cursor': 'opaque'},
            {'results': [{'path': GP+'b', 'target_display_name': 'B'}]}]
        result = read(client, dict(VM, external_id=None, compute_ids=['externalId:a', 'hostLocalId:ignored']))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([r['path'] for r in result['items']], [GP+'a', GP+'b'])
        self.assertEqual(client.get.call_args_list[0].args,
                         ('/infra/virtual-machine-group-associations', {'vm_external_id':'a', 'page_size':1000}))
        self.assertEqual(client.get.call_args_list[1].args[1]['cursor'], 'opaque')
        for vm in ({'id':'a', 'display_name':'a'}, dict(VM, compute_ids=['externalId:another']),
                   dict(VM, external_id=None, compute_ids=['hostLocalId:a'])):
            client.reset_mock()
            self.assertEqual(read(client, vm)['status'], 'unavailable')
            client.get.assert_not_called()

    def test_pagination_errors_retain_positive_evidence_and_never_invent_zero(self):
        for second in (collector.AuditError('sensitive response', status_code=503),
                       {'results':[], 'result_count':3},
                       {'results':[{'path':GP+'a'}], 'cursor':'same'},
                       {'results':[{'path':GP+'bad', 'is_valid':'false'}]}):
            client = Mock()
            client.get.side_effect = [{'results':[{'path':GP+'a'}], 'result_count':2, 'cursor':'same'}, second]
            result = read(client, VM)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['items'][0]['path'], GP+'a')
            self.assertNotIn('sensitive', str(result))
        client = Mock(); client.get.return_value = {'results':[], 'result_count':0}
        self.assertEqual(read(client, VM)['status'], 'complete')
        client.get.return_value = {'results':None}
        self.assertEqual(read(client, VM)['status'], 'unavailable')
        client.get.return_value = {'results':[], 'result_count':0}
        self.assertEqual(read(client, VM, testing=True)['status'], 'partial')

    def test_scope_permissions_ambiguity_and_collection_deadline(self):
        client = Mock(); client.get.side_effect = collector.AuditError('denied', status_code=403)
        vms = [dict(VM, path='/vm/'+str(i), external_id=str(i)) for i in range(100)]
        meta, stats = collect(client, vms, workers=8)
        self.assertEqual(stats['requests'],1)
        self.assertEqual(stats['statuses'], {'unavailable':100})
        client.reset_mock(); client.get.side_effect = None
        meta, stats = collect(client, [VM, dict(VM, path='/other/path')])
        client.get.assert_not_called()
        self.assertEqual(stats['statuses'], {'unavailable':2})
        client.get.return_value = {'results':[], 'result_count':0}
        result = read(client, dict(VM, path='/orgs/o/projects/p/infra/realized-state/virtual-machines/a'))
        self.assertEqual(result['status'],'complete')
        self.assertEqual(client.get.call_args.args[0],'/orgs/o/projects/p/infra/virtual-machine-group-associations')
        self.assertEqual(read(client, dict(VM, path='/invalid/realized-state/virtual-machines/a'))['status'],'unavailable')
        from .request_pacing import CollectionDeadlineExceeded
        client.get.side_effect = CollectionDeadlineExceeded('Collection expired')
        with self.assertRaises(CollectionDeadlineExceeded):
            collect(client, [VM])

    def test_per_vm_completeness_and_inverse_do_not_use_global_sample_status(self):
        good = {'status':'complete', 'items':[{'path':GP+'a','name':'A'}], 'notes':[], 'checked_at':'now'}
        bad = {'status':'unavailable', 'items':[], 'notes':['Permission failure'], 'checked_at':'now'}
        meta = {'version':1,'method':'vm_associations','vms':{'/vm/a':good, '/vm/b':bad}}
        tags = {'virtual_machines':[dict(VM,path='/vm/a'),dict(VM,path='/vm/b',external_id='b')], 'vm_inventory_complete':True}
        groups = [{'path':GP+'a', 'name':'A', 'audit_exclusions':['System-owned']}]
        apply_to_groups(tags,groups,meta)
        self.assertEqual(groups[0]['resolved_membership']['endpoints']['virtual-machines']['status'],'partial')
        rows = list(collector.iter_vm_inventory_rows({'tags':tags,'inventory':{'groups':groups},'membership_evidence':meta}))
        self.assertEqual(rows[0]['resolved_group_count'],1)
        self.assertEqual(rows[0]['resolved_membership_status'],'complete')
        self.assertIsNone(rows[1]['resolved_group_count'])
        self.assertEqual(rows[1]['resolved_membership_status'],'unavailable')

    def test_vm_inventory_warning_prevents_a_complete_inverse(self):
        client = Mock(inventory_warnings=[{'endpoint':'/infra/realized-state/virtual-machines'}])
        tags = collector.audit_tags(client, [], inventories=[([],None),([VM],None)])
        self.assertFalse(tags['vm_inventory_complete'])

    def test_a_failed_vm_does_not_downgrade_a_successful_vm_and_invalid_ids_do_not_prevent_permission_probe(self):
        client = Mock()
        def get(path, params):
            if params['vm_external_id']=='bad':
                raise collector.AuditError('Not found',status_code=404)
            return {'results':[{'path':GP+'good'}], 'result_count':1}
        client.get.side_effect = get
        metadata, stats = collect(client,[dict(VM,external_id='bad'),dict(VM,path='/vm/good',external_id='good')])
        self.assertEqual(metadata['vms']['/vm/good']['status'],'complete')
        self.assertEqual(stats['statuses'],{'complete':1,'unavailable':1})
        client.reset_mock();client.get.side_effect = collector.AuditError('Denied',status_code=403)
        metadata, stats = collect(client,[{'path':'/vm/missing-id'}, *[
            dict(VM,path='/vm/'+str(i),external_id=str(i)) for i in range(100)]])
        self.assertEqual(stats['requests'],1)
        self.assertEqual(stats['statuses'],{'unavailable':101})


def synthetic_collection(vm_count=2, real_groups=4):
    """Use the real collection orchestrator; replace only transport and DFW input."""
    vms = [dict(VM, path='/infra/realized-state/virtual-machines/'+str(i), external_id=str(i),
                display_name='Synthetic '+str(i)) for i in range(vm_count)]
    groups = [{'path':GP+str(i), 'display_name':'Actual '+str(i),
               'is_default': i == 3, 'expression':[{'resource_type':'ExternalIDExpression',
               'member_type':'VirtualMachine','external_ids':['0']}]} for i in range(real_groups)]
    groups += [{'path':GP+'tag-'+str(i), 'display_name':'Tag candidate '+str(i), 'expression':[
        {'resource_type':'Condition','member_type':'VirtualMachine','key':'Tag','operator':'EQUALS','value':'env|prod'}]}
        for i in range(25)]
    client = Mock(membership_hints={}, inventory_warnings=[])
    client.items.side_effect = lambda path, *args, **kwargs: {
        '/infra/domains':[{'path':'/infra/domains/default'}],
        '/infra/domains/default/groups':groups, '/infra/services':[],
        '/infra/tags':[], '/infra/realized-state/virtual-machines':vms}[path]
    def get(path, params):
        if path.endswith('/virtual-machine-group-associations'):
            return {'results':[{'path': GP+str(i), 'target_display_name':'Actual '+str(i)} for i in range(real_groups)], 'result_count':real_groups}
        return {'results':[], 'result_count':0}
    client.get.side_effect = get
    rule = {'path':'/infra/domains/default/security-policies/p/rules/r', 'name':'Synthetic rule',
        'policy_path':'/infra/domains/default/security-policies/p', 'policy_name':'Synthetic policy',
        'rule_id':42, 'policy_rule_id':'r','source_groups':[GP+'3'],'destination_groups':['ANY'],
        'scope':['ANY'],'services':['ANY'],'action':'ALLOW','disabled':False,'hit_status':'unknown','notes':[]}
    with patch.object(collector,'verified_configuration_search',return_value=(groups,{'mode':'all_types'})), \
         patch.object(collector,'audit_dfw',return_value=({'rules':[rule],'policies':[], 'errors':[]},[])):
        report = collector.audit(client, workers=4)
    return report, client


class FullCollectionAssociationTests(SimpleTestCase):
    def test_complete_collection_saves_four_groups_not_25_tag_candidates_and_reuses_positives(self):
        report, client = synthetic_collection()
        rows = collector.vm_inventory_rows(report)
        self.assertEqual(len(rows),2)
        for row in rows:
            self.assertEqual(row['resolved_group_count'],4)
            self.assertEqual(row['resolved_membership_status'],'complete')
            self.assertEqual(row['related_groups'],[])  # Discard obsolete tag-candidate expansion.
            self.assertEqual(row['tag_count'],1)
        self.assertEqual(len(report['tags']['objects'][0]['group_conditions']),25)
        stats = report['performance']['membership']
        self.assertEqual(stats['positive_association_reuses'],3)  # Fourth group excluded only from cleanup.
        self.assertEqual(stats['vm_associations']['requests'],2)
        self.assertFalse(any('/groups/0/members/' in call.args[0] for call in client.get.call_args_list))
        self.assertEqual(sum(call.args[0]=='/infra/realized-state/virtual-machines' for call in client.items.call_args_list),1)
        inverse = report['inventory']['groups'][3]['resolved_membership']['endpoints']['virtual-machines']
        self.assertEqual(inverse['status'],'complete')
        self.assertEqual(len(inverse['items']),2)
        for group in report['objects']:
            if group['path'].startswith(GP+'tag-'):
                self.assertEqual(group['membership'],'empty')  # Missing association does not bypass probes.


class SavedAssociationProjectionTests(TestCase):
    def test_new_snapshot_counts_members_and_firewall_references_work_without_current_nsx(self):
        from .models import Environment, Snapshot, SnapshotRecord
        from .snapshot_index import build
        from .vm_relationships import read as vm_read
        from .group_memberships import read as group_read
        from .current_membership import MembershipLookup, MembershipRelation
        report, _ = synthetic_collection()
        env = Environment.objects.create(name='Offline association check',slug='offline-associations',manager='https://synthetic.invalid')
        snapshot = Snapshot.objects.create(environment=env,generated_at=timezone.now(),report=report)
        with patch('inventory.deletion.connection',side_effect=AssertionError('Browsing must not call NSX')):
            build(snapshot)
            row = SnapshotRecord.objects.get(snapshot=snapshot,view='vms',compact__path=VM['path'].rsplit('/',1)[0]+'/0')
            self.assertEqual(row.compact['resolved_group_count'],4)
            self.assertEqual(row.compact['resolved_membership_status'],'complete')
            self.assertEqual(len(vm_read(snapshot.pk,row.ordinal,'resolved_groups')['items']),4)
            rules = vm_read(snapshot.pk,row.ordinal,'confirmed_rules')
            self.assertEqual(rules['status'],'complete')
            self.assertEqual(len(rules['items']),1)
            self.assertEqual(rules['items'][0]['name'],'Synthetic rule')
            members = group_read(snapshot.pk,GP+'3')
            self.assertEqual(len(members['items']),2)
            self.assertEqual(members['status'],'complete')
        self.assertFalse(MembershipLookup.objects.exists())
        self.assertFalse(MembershipRelation.objects.exists())
