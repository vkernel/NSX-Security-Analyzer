from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock, patch
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.utils import timezone
from .models import (Environment, Snapshot, SnapshotRecord, MembershipRelation,
                     MembershipLookup, AuditJob, GroupMembershipSession)
from .current_membership import save, subject, cached
from .collector import AuditError

GP = '/infra/domains/default/groups/'


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class GroupPagingTests(TestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='Paging synthetic',slug='paging',manager='https://synthetic.invalid')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={'immutable':True})
        for i in range(2):
            SnapshotRecord.objects.create(snapshot=self.snapshot,view='inventory',ordinal=i,name='Group '+str(i),
                compact={'path':GP+str(i)},data={'kind':'group'},columns=[],sort_values={})
        self.owner=subject(self.snapshot,'group',GP+'0')
        self.user=get_user_model().objects.create_user('paging-operator',is_staff=True)
        self.client.force_login(self.user)
        self.url=reverse('current-membership',args=[self.snapshot.pk])
        self.nsx=Mock()
        self.offset=0
        self.requests=[]
        self.nsx.get.side_effect=self.page
        self.connect=patch('inventory.deletion.connection',self.connection);self.connect.start();self.addCleanup(self.connect.stop)

    @contextmanager
    def connection(self,env): yield self.nsx

    def page(self,path,params):
        self.requests.append((path,dict(params)))
        start=int(params.get('cursor','0'));end=min(start+100,225)
        return {'results':[{'external_id':f'vm-{i:04}','display_name':('Z' if i<100 else 'A')+str(i)} for i in range(start,end)],
                **({'result_count':225} if start==0 else {}),**({'cursor':str(end)} if end<225 else {})}

    def start(self):
        response=self.client.post(self.url,{'kind':'group','path':GP+'0'})
        self.assertEqual(response.status_code,200,response.content)
        return response.json()

    def get(self,page=0,token=None,**extra):
        return self.client.get(self.url,{'kind':'group','path':GP+'0','page':page,**({'paging_session':token} if token else {}),**extra})

    def advance(self,data,page):
        return self.client.post(self.url,{'kind':'group','path':GP+'0','page':page,'paging_session':data['paging_session'],'refresh_mode':'continue'})

    def test_first_100_support_four_pages_then_one_read_for_next_chunk_with_stable_positions(self):
        data=self.start()
        self.assertEqual(self.nsx.get.call_count,1)
        self.assertEqual(self.requests[0][1],{'page_size':100})
        self.assertEqual(data['loaded_count'],100);self.assertEqual(data['reported_count'],225)
        self.assertEqual(data['status'],'partial');self.assertEqual(len(data['items']),25)
        self.assertNotIn('cursor',data);self.assertEqual(data['items'][0]['external_id'],'vm-0000')
        token=data['paging_session']
        for page in range(4):
            result=self.get(page,token).json()
            self.assertEqual(result['items'][0]['external_id'],f'vm-{page*25:04}')
        self.assertEqual(self.nsx.get.call_count,1)
        self.assertTrue(self.get(4,token).json()['needs_more'])
        result=self.advance(data,4)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['loaded_count'],200)
        self.assertEqual(result.json()['items'][0]['external_id'],'vm-0100')
        self.assertEqual(self.requests[-1][1]['cursor'],'100')
        self.assertEqual(self.get(0,token).json()['items'][0]['external_id'],'vm-0000')
        self.assertEqual(self.nsx.get.call_count,2)
        self.assertEqual(self.advance(data,4).status_code,200)  # Concurrent/repeated next request reuses loaded window.
        self.assertEqual(self.nsx.get.call_count,2)
        final=self.advance(data,8).json()
        self.assertEqual(final['status'],'complete');self.assertEqual(final['loaded_count'],225)
        self.assertFalse(final['has_next']);self.assertEqual(self.nsx.get.call_count,3)
        self.snapshot.refresh_from_db();self.assertEqual(self.snapshot.report,{'immutable':True})

    def test_partial_pages_do_not_remove_old_edges_but_complete_traversal_does(self):
        save(self.env,'group',self.owner,[{'identity':'old','external_id':'old','name':'Old'}],'complete',[])
        data=self.start()
        self.assertTrue(MembershipRelation.objects.filter(vm__external_id='old',present=True).exists())
        self.advance(data,4);self.advance(data,8)
        self.assertFalse(MembershipRelation.objects.filter(vm__external_id='old',present=True).exists())
        self.assertEqual(MembershipRelation.objects.filter(present=True).count(),225)

    def test_tokens_are_bound_to_group_snapshot_manager_and_refresh(self):
        data=self.start();token=data['paging_session']
        for extras in [{'path':GP+'1'},{'paging_session':'bad-token'}]:
            self.assertEqual(self.get(0,token,**extras).status_code,409)
        other=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={})
        SnapshotRecord.objects.create(snapshot=other,view='inventory',ordinal=0,name='Other',compact={'path':GP+'0'},data={'kind':'group'},columns=[],sort_values={})
        self.assertEqual(self.client.get(reverse('current-membership',args=[other.pk]),{'kind':'group','path':GP+'0','paging_session':token}).status_code,409)
        newer=self.start();self.assertNotEqual(newer['paging_session'],token)
        self.assertTrue(self.get(0,token).json()['stale'])
        before=self.nsx.get.call_count;self.assertEqual(self.advance(data,4).status_code,409);self.assertEqual(self.nsx.get.call_count,before)
        self.env.manager='https://other.invalid';self.env.save()
        self.assertEqual(self.get(0,newer['paging_session']).status_code,409)

    def test_expired_session_keeps_loaded_pages_but_does_not_follow_cursor(self):
        data=self.start()
        GroupMembershipSession.objects.filter(pk=data['paging_session']).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(len(self.get(1,data['paging_session']).json()['items']),25)
        self.assertFalse(self.get(3,data['paging_session']).json()['has_next'])
        self.assertEqual(self.advance(data,4).status_code,409)
        self.assertEqual(self.nsx.get.call_count,1)

    def test_changed_vm_observation_invalidates_group_cursor_without_losing_confirmed_vm_groups(self):
        data=self.start()
        vm={'identity':'vm-0000','external_id':'vm-0000','name':'VM'}
        save(self.env,'vm',vm,[],'complete',[])
        self.assertEqual(cached(self.snapshot,'vm',vm)['saved_count'],0)
        self.assertTrue(self.get(0,data['paging_session']).json()['stale'])
        self.assertEqual(self.advance(data,4).status_code,409)
        self.assertEqual(self.nsx.get.call_count,1)

    def test_transient_error_retains_cursor_and_retry_fetches_same_page(self):
        data=self.start()
        self.nsx.get.side_effect=AuditError('secret error',503)
        response=self.advance(data,4)
        self.assertEqual(response.status_code,409);self.assertNotIn('secret',response.content.decode())
        self.assertEqual(self.get(0,data['paging_session']).json()['loaded_count'],100)
        self.nsx.get.side_effect=self.page
        self.assertEqual(self.advance(data,4).status_code,200)
        self.assertEqual(self.requests[-1][1]['cursor'],'100')

    def test_invalid_totals_duplicates_or_cursor_never_retire_earlier_positives(self):
        cases=[{'results':[],'result_count':224,'cursor':'next'},
               {'results':[{'external_id':'vm-0000'}],'cursor':'next'},
               {'results':[{'external_id':'unique'}],'cursor':'100'},
               {'results':[{'external_id':'unique'}],'result_count':225}]
        for response in cases:
            self.nsx.get.side_effect=self.page;data=self.start()
            self.nsx.get.side_effect=lambda *args:response
            self.assertEqual(self.advance(data,4).status_code,409)
            result=self.get(0,data['paging_session']).json()
            self.assertEqual(result['status'],'partial');self.assertGreaterEqual(result['loaded_count'],100)
            self.assertTrue(MembershipRelation.objects.filter(vm__external_id='vm-0000',present=True).exists())

    def test_short_pages_and_unknown_total_remain_bounded_and_do_not_skip_members(self):
        def short(path,params):
            start=int(params.get('cursor','0'));end=start+10
            return {'results':[{'external_id':str(i)} for i in range(start,end)],'cursor':str(end)}
        self.nsx.get.side_effect=short;data=self.start()
        self.assertEqual(self.nsx.get.call_count,3);self.assertEqual(data['loaded_count'],30)
        self.assertIsNone(data['reported_count']);self.assertEqual(len(data['items']),25)
        result=self.advance(data,1).json()
        self.assertEqual([item['external_id'] for item in result['items']],[str(i) for i in range(25,50)])
        self.assertEqual(result['status'],'partial')

    def test_read_only_get_never_reads_nsx_and_permissions_collection_priority_still_apply(self):
        data=self.start();self.user.is_staff=False;self.user.save()
        result=self.get(3,data['paging_session']).json()
        self.assertFalse(result['has_next']);self.assertFalse(result['can_continue']);self.assertFalse(result['can_refresh'])
        self.assertEqual(self.advance(data,4).status_code,403)
        c=Client(enforce_csrf_checks=True);c.force_login(self.user)
        self.assertEqual(c.post(self.url,{'kind':'group','path':GP+'0'}).status_code,403)
        self.user.is_staff=True;self.user.save();AuditJob.objects.create(environment=self.env,status='queued')
        self.assertEqual(self.advance(data,4).status_code,409);self.assertEqual(self.nsx.get.call_count,1)

    def test_failed_initial_refresh_retains_previous_positive_cache_and_zero_requires_complete_response(self):
        save(self.env,'group',self.owner,[{'identity':'old','external_id':'old','name':'Old'}],'complete',[])
        self.nsx.get.side_effect=AuditError('private response',403)
        result=self.start()
        self.assertEqual(result['status'],'unavailable');self.assertEqual(result['items'][0]['external_id'],'old');self.assertTrue(result['stale'])
        with patch('inventory.deletion.connection',side_effect=ValidationError('Environment is paused.')):
            self.assertEqual(self.client.post(self.url,{'kind':'group','path':GP+'0'}).status_code,400)
        self.assertEqual(self.get().json()['items'][0]['external_id'],'old')
        self.nsx.get.side_effect=lambda *args:{'results':[],'result_count':0}
        result=self.start();self.assertEqual(result['status'],'complete');self.assertEqual(result['loaded_count'],0)
        self.assertFalse(MembershipRelation.objects.filter(present=True).exists())
