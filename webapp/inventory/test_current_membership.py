from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, SnapshotRecord, MembershipLookup, MembershipRelation, AuditJob
from .current_membership import fetch, save, cached, subject, refresh, namespace, digest
from .collector import AuditError

GP = '/infra/domains/default/groups/'


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CurrentMembershipTests(TestCase):
    def setUp(self):
        self.env = Environment.objects.create(name='Synthetic',slug='synthetic',manager='https://synthetic.invalid')
        self.snapshot = Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={'immutable':True})
        self.vm = SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=1,view='vms',name='VM',
            compact={'path':'/vm/a','resolved_group_count':2,'resolved_membership_status':'partial'},
            data={'vm_details':{'external_id':'a'},'resolved_groups':[{'path':GP+'0'},{'path':GP+'1'}],
                  'related_groups':[{'path':GP+str(i)} for i in range(25)]},columns=[],sort_values={})
        for i in range(25):
            SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=10+i,view='inventory',name='Group '+str(i),
                compact={'path':GP+str(i)},data={'kind':'group','audit_exclusions':['System-owned'] if i==3 else []},columns=[],sort_values={})
        self.user = get_user_model().objects.create_user('operator',is_staff=True)
        self.client.force_login(self.user)
        self.url = reverse('current-membership',args=[self.snapshot.pk])
        self.owner = subject(self.snapshot,'vm',1)
        self.four = [{'identity':GP+str(i),'path':GP+str(i),'name':'Group '+str(i)} for i in range(4)]

    @contextmanager
    def connection(self,env):
        yield self.nsx

    def test_four_actual_memberships_and_25_tag_references_with_one_targeted_read(self):
        self.nsx=Mock(); self.nsx.get.return_value={'results':[{'path':g['path'],'target_display_name':g['name']} for g in self.four],'result_count':4}
        before=self.snapshot.report.copy()
        with patch('inventory.deletion.connection',self.connection):
            result=self.client.post(self.url,{'kind':'vm','id':1})
            self.assertEqual(result.status_code,200,result.content)
            self.assertEqual(result.json()['saved_count'],4)
            self.assertEqual(result.json()['status'],'complete')
            self.client.get(self.url,{'kind':'vm','id':1})
            self.assertEqual(self.nsx.get.call_count,1)
            self.assertEqual(self.nsx.get.call_args.args[0],'/infra/virtual-machine-group-associations')
            self.assertEqual(self.nsx.get.call_args.args[1]['vm_external_id'],'a')
        group=self.client.get(self.url,{'kind':'group','path':GP+'3'}).json()
        self.assertEqual(group['items'][0]['vm_path'],'/vm/a')
        self.assertEqual(group['status'],'partial')  # One VM check cannot complete the entire group.
        self.assertEqual(MembershipRelation.objects.filter(present=True).count(),4)
        self.snapshot.refresh_from_db();self.vm.refresh_from_db()
        self.assertEqual(self.snapshot.report,before)
        self.assertEqual(self.vm.compact['resolved_group_count'],2)
        self.assertEqual(len(self.vm.data['related_groups']),25)

    def test_get_is_cache_only_and_environment_and_manager_are_isolated(self):
        with patch('inventory.deletion.connection') as connect:
            self.assertEqual(self.client.get(self.url,{'kind':'vm','id':1}).json()['status'],'not_collected')
            connect.assert_not_called()
        save(self.env,'vm',self.owner,self.four,'complete',[])
        self.env.manager='https://new-manager.invalid';self.env.save()
        self.assertEqual(self.client.get(self.url,{'kind':'vm','id':1}).json()['saved_count'],0)
        self.assertEqual(MembershipRelation.objects.count(),4)

    def test_partial_refresh_preserves_positives_complete_refresh_removes_and_invalidates_inverse(self):
        save(self.env,'vm',self.owner,self.four,'complete',[])
        group=subject(self.snapshot,'group',GP+'0')
        save(self.env,'group',group,[self.owner],'complete',[])
        save(self.env,'vm',self.owner,self.four[1:],'partial',['Page failed'])
        self.assertEqual(cached(self.snapshot,'vm',self.owner)['saved_count'],4)
        save(self.env,'vm',self.owner,self.four[1:],'complete',[])
        self.assertEqual(cached(self.snapshot,'vm',self.owner)['saved_count'],3)
        inverse=cached(self.snapshot,'group',group)
        self.assertEqual(inverse['saved_count'],0);self.assertEqual(inverse['status'],'partial')

    def test_unrelated_incomplete_vm_does_not_change_complete_coverage(self):
        save(self.env,'vm',self.owner,self.four,'complete',[])
        save(self.env,'vm',dict(self.owner,identity='other',external_id='other'),[],'unavailable',['Failed'])
        self.assertEqual(cached(self.snapshot,'vm',self.owner)['status'],'complete')

    def test_cached_pages_are_bounded_and_stale_is_visible(self):
        rows=[{'identity':GP+str(i),'path':GP+str(i),'name':str(i)} for i in range(53)]
        lookup=save(self.env,'vm',self.owner,rows,'complete',[])
        lookup.expires_at=timezone.now()-timedelta(seconds=1);lookup.save()
        result=cached(self.snapshot,'vm',self.owner)
        self.assertEqual(len(result['items']),25);self.assertTrue(result['has_next']);self.assertTrue(result['stale'])
        self.assertEqual(len(cached(self.snapshot,'vm',self.owner,2)['items']),3)

    def test_cache_warmer_skips_fresh_subjects_and_advances_to_unchecked_vms(self):
        save(self.env,'vm',self.owner,self.four,'complete',[])
        SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=2,view='vms',name='B',compact={'path':'/vm/b'},
            data={'vm_details':{'external_id':'b'}},columns=[],sort_values={})
        with patch('inventory.management.commands.refresh_membership_cache.refresh') as warm, patch('inventory.management.commands.refresh_membership_cache.time.sleep'):
            call_command('refresh_membership_cache',environment=self.env.pk,limit=1)
        warm.assert_called_once()
        self.assertEqual(warm.call_args.args[2]['external_id'],'b')

    def test_pending_collection_and_manager_lease_block_nsx_reads(self):
        AuditJob.objects.create(environment=self.env,status='running')
        with patch('inventory.deletion.connection') as connect:
            response=self.client.post(self.url,{'kind':'vm','id':1})
            self.assertEqual(response.status_code,409);connect.assert_not_called()
            with patch('inventory.management.commands.refresh_membership_cache.time.sleep'), patch('inventory.current_membership.refresh') as warm:
                call_command('refresh_membership_cache',environment=self.env.pk,limit=10)
                warm.assert_not_called()
        AuditJob.objects.all().delete()
        @contextmanager
        def busy(env): yield False
        with patch('inventory.current_membership.manager_lease',busy),patch('inventory.deletion.connection') as connect:
            self.assertEqual(self.client.post(self.url,{'kind':'vm','id':1}).status_code,409);connect.assert_not_called()

    def test_staff_csrf_archive_and_snapshot_subject_validation(self):
        self.user.is_staff=False;self.user.save()
        self.assertEqual(self.client.post(self.url,{'kind':'vm','id':1}).status_code,403)
        self.assertEqual(self.client.post(self.url,{'kind':'group','path':GP+'missing'}).status_code,404)
        self.assertEqual(self.client.get(self.url,{'kind':'vm','id':'bad'}).status_code,400)
        c=Client(enforce_csrf_checks=True);c.force_login(self.user)
        self.assertEqual(c.post(self.url,{'kind':'vm','id':1}).status_code,403)
        self.snapshot.browsing_status='archived';self.snapshot.save()
        self.assertEqual(self.client.get(self.url,{'kind':'vm','id':1}).status_code,409)

    def test_confirmed_rules_exclude_tag_only_rules_and_allow_system_group(self):
        for i,g in enumerate([GP+'3',GP+'20']):
            SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=50+i,view='dfw',name='Rule '+str(i),
                compact={'path':'/infra/rules/'+str(i)},data={'path':'/infra/rules/'+str(i),'name':'Rule '+str(i),
                    'source_groups':[g],'destination_groups':[],'scope':[],'services':['ANY']},columns=[],sort_values={})
        save(self.env,'vm',self.owner,self.four,'complete',[])
        result=self.client.get(self.url,{'kind':'vm','id':1,'section':'firewall'}).json()
        self.assertEqual([item['name'] for item in result['items']],['Rule 0'])
        self.assertIn('saved firewall',result['notes'][-1])
        api=reverse('snapshot-data',args=[self.snapshot.pk])
        self.assertEqual(self.client.get(api,{'op':'current-member-rule','id':1,'path':'/infra/rules/1'}).status_code,404)
        self.assertEqual(self.client.get(api,{'op':'current-member-rule','id':1,'path':'/infra/rules/0'}).json()['details']['services'],['ANY'])
        saved=self.client.get(api,{'op':'vm-relationships','id':1,'section':'confirmed_rules'}).json()
        self.assertEqual(saved['items'],[])  # Saved two groups did not include Group 3.


class AssociationPagingTests(SimpleTestCase):
    owner={'identity':'vm','external_id':'vm','scope':'/infra'}
    def test_cursor_pages_and_positive_partial_failures(self):
        client=Mock();client.get.side_effect=[{'results':[{'path':GP+'a'}],'result_count':2,'cursor':'opaque'},
            {'results':[{'path':GP+'b'}],'result_count':2}]
        items,status,notes=fetch(client,'vm',self.owner)
        self.assertEqual(len(items),2);self.assertEqual(status,'complete');self.assertEqual(client.get.call_args.args[1]['cursor'],'opaque')
        client.get.side_effect=[{'results':[{'path':GP+'a'}],'result_count':2,'cursor':'next'},AuditError('secret response',403)]
        items,status,notes=fetch(client,'vm',self.owner)
        self.assertEqual(len(items),1);self.assertEqual(status,'partial');self.assertNotIn('secret',str(notes))

    def test_malformed_counts_duplicates_and_cursor_loops_are_not_complete(self):
        cases=[{'results':[{'path':'bad'}],'result_count':1}, {'results':[],'result_count':2},
            {'results':[{'path':GP+'a'},{'path':GP+'a'}],'result_count':2}, {'results':[],'result_count':True}]
        for page in cases:
            _,status,_=fetch(Mock(get=Mock(return_value=page)),'vm',self.owner)
            self.assertNotEqual(status,'complete')
        client=Mock();client.get.return_value={'results':[{'path':GP+'a'}],'cursor':'same','result_count':2}
        _,status,_=fetch(client,'vm',self.owner);self.assertEqual(status,'partial');self.assertEqual(client.get.call_count,2)

    def test_read_deadline_and_page_cap_are_bounded(self):
        client=Mock();client.get.return_value={'results':[],'cursor':'next'}
        _,status,_=fetch(client,'vm',self.owner,max_pages=1)
        self.assertNotEqual(status,'complete');self.assertEqual(client.get.call_count,1)
        client.get.side_effect=TimeoutError('deadline')
        self.assertEqual(fetch(client,'vm',self.owner)[1],'unavailable')
