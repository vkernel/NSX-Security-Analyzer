from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from . import analysis_index
from .application_maps import cached_map, MAX_VARIANTS
from .models import Environment, Snapshot, SnapshotRecord, SnapshotApplicationMap, SegmentationSettings
from .segmentation import build


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ApplicationMapTests(TestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='Map fixture',slug='map-fixture',manager='map.invalid')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={},summary={})
        self.user=get_user_model().objects.create_superuser('map-admin',password='test-only')
        self.client.force_login(self.user)
        SegmentationSettings.objects.create(environment=self.env,grouping_scope='Application')
        self.group('Portal','/groups/portal','Portal')
        self.group('Data','/groups/data','Data')
        self.rule=self.row('Lazy SQL rule','/rules/sql',configuration={'rule':{'source_groups':['/groups/portal'],'destination_groups':['/groups/data'],'services':['ANY'],'action':'ALLOW'}})
        self.row('Disabled deny','/rules/off',configuration={'rule':{'source_groups':['/groups/portal'],'destination_groups':['/groups/data'],'services':['ANY'],'action':'DROP','disabled':True}})
        self.url=reverse('segmentation',args=[self.env.pk])
        self.params={'snapshot':str(self.snapshot.pk),'tab':'map'}

    def row(self,name,path,**data):
        return SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=SnapshotRecord.objects.count(),view='inventory',name=name,sort_name=name.casefold(),
            data=dict(name=name,path=path,**data),compact={},columns=[],sort_values={},search_basic=name,search_evidence='')

    def group(self,name,path,app):
        return self.row(name,path,kind='group',configuration={'expression':[{'resource_type':'Condition','member_type':'VirtualMachine','key':'Tag','operator':'EQUALS','value':'Application|'+app}]})

    def detail_url(self,**params):
        response=self.client.get(self.url,dict(self.params,**params))
        return response.context['applications']['edges'][0]['details_url']

    def test_cache_hit_uses_no_source_projection_or_graph_rebuild(self):
        first=cached_map(self.snapshot,scope='Application')
        with patch('inventory.application_maps.build',side_effect=AssertionError('Rebuilt map')),patch('inventory.analysis_index.load',side_effect=AssertionError('Loaded raw projection')):
            with CaptureQueriesContext(connection) as queries:
                again=cached_map(Snapshot.objects.only('pk','browsing_status').get(pk=self.snapshot.pk),scope='Application')
        self.assertEqual(first,again)
        self.assertFalse(any('inventory_snapshotrecord' in q['sql'] for q in queries))
        payload=SnapshotApplicationMap.objects.get().payload
        self.assertNotIn('configuration',str(payload))
        self.assertNotIn('Lazy SQL rule',str(payload))

    def test_compact_graph_preserves_counts_actions_coordinates_and_limits(self):
        for i in range(60): self.group(f'Portal {i:02}',f'/groups/extra-{i}','Portal')
        original=build(self.snapshot,scope='Application',application_mode=True)['applications']
        cached=cached_map(self.snapshot,scope='Application')['applications']
        self.assertEqual(cached['group_count'],original['group_count'])
        self.assertEqual(cached['unresolved'],original['unresolved'])
        portal=next(n for n in cached['nodes'] if n['name']=='Portal')
        self.assertEqual(portal['group_count'],61);self.assertEqual(len(portal['groups']),50)
        for old,new in zip(original['edges'],cached['edges']):
            for key in ('rule_count','state','internal','x1','y1','x2','y2'): self.assertEqual(old[key],new[key])
        response=self.client.get(self.url,self.params)
        self.assertContains(response,'61 groups')
        self.assertContains(response,'First 50 groups shown')

    def test_filters_and_scope_have_separate_bounded_variants(self):
        normal=cached_map(self.snapshot,scope='Application')
        disabled=cached_map(self.snapshot,scope='Application',include_disabled=True)
        self.assertEqual(normal['applications']['edges'][0]['rule_count'],1)
        self.assertEqual(disabled['applications']['edges'][0]['rule_count'],2)
        self.assertEqual(disabled['applications']['edges'][0]['state'],'allow')  # Disabled rules do not change the active-action label.
        self.assertTrue(cached_map(self.snapshot,scope='Other')['applications']['edges'][0]['internal'])
        self.assertFalse(cached_map(self.snapshot,scope='Application',service='no-such-service')['applications']['edges'])
        self.assertFalse(cached_map(self.snapshot,scope='Application',hidden_paths=['/groups/data'])['applications']['edges'])
        for i in range(MAX_VARIANTS+2): cached_map(self.snapshot,query=f'query {i}',scope='Application')
        self.assertEqual(SnapshotApplicationMap.objects.count(),MAX_VARIANTS)

    def test_map_page_contains_no_connection_rule_html_until_opened(self):
        response=self.client.get(self.url,self.params)
        self.assertNotContains(response,'Lazy SQL rule')
        self.assertContains(response,'data-connection-details')
        detail_url=response.context['applications']['edges'][0]['details_url']
        with patch('inventory.application_maps.build',side_effect=AssertionError('Rebuilt graph')):
            details=self.client.get(detail_url)
        self.assertContains(details,'Lazy SQL rule')
        self.assertNotContains(details,'Disabled deny')
        self.assertNotContains(details,'<html')
        self.assertContains(self.client.get(self.detail_url(disabled='1')),'Disabled deny')

    def test_rule_detail_links_reviews_and_csv_remain_available(self):
        response=self.client.get(self.url,dict(self.params,rule='/rules/sql'))
        self.assertContains(response,'Open saved rule evidence')
        self.assertEqual(response.context['selected']['name'],'Lazy SQL rule')
        self.assertContains(self.client.get(self.url,dict(self.params,export='csv')),'Lazy SQL rule')

    def test_eviction_rebuilds_matching_connection_and_source_changes_reject_old_links(self):
        url=self.detail_url()
        SnapshotApplicationMap.objects.all().delete()
        self.assertContains(self.client.get(url),'Lazy SQL rule')
        self.rule.data['configuration']['rule']['action']='DROP';self.rule.save()
        self.assertFalse(SnapshotApplicationMap.objects.exists())
        self.assertContains(self.client.get(url),'Reopen the application map',status_code=409)
        self.assertEqual(cached_map(self.snapshot,scope='Application')['applications']['edges'][0]['state'],'drop')

    def test_snapshot_environment_authentication_and_archiving_are_enforced(self):
        url=self.detail_url()
        self.assertEqual(self.client.get(url.replace('version=1','version=0')).status_code,409)
        other=Environment.objects.create(name='Other',slug='other-map',manager='other-map.invalid')
        self.assertEqual(self.client.get(url.replace(f'/environments/{self.env.pk}/',f'/environments/{other.pk}/')).status_code,404)
        self.snapshot.browsing_status='archived';self.snapshot.save(update_fields=['browsing_status'])
        self.assertFalse(SnapshotApplicationMap.objects.exists())
        self.assertEqual(self.client.get(url).status_code,409)
        self.client.logout();self.assertEqual(self.client.get(url).status_code,302)

    def test_preparation_warms_current_scope_and_version_upgrade_invalidates_maps(self):
        analysis_index.prepare(self.snapshot)
        with patch('inventory.application_maps.build',side_effect=AssertionError('Prepared map was not reused')):
            cached_map(self.snapshot,scope='Application')
        from .models import SnapshotAnalysisIndex
        SnapshotAnalysisIndex.objects.update(version=0)
        analysis_index.load(self.snapshot,'groups')
        self.assertFalse(SnapshotApplicationMap.objects.exists())

    def test_source_change_during_detail_loading_does_not_return_mixed_evidence(self):
        url=self.detail_url()
        original=analysis_index.load
        def changing_source(*args,**kwargs):
            result=original(*args,**kwargs)
            analysis_index.invalidate(self.snapshot.pk)
            return result
        with patch('inventory.analysis_index.load',side_effect=changing_source):
            response=self.client.get(url)
        self.assertContains(response,'Reopen the application map',status_code=409)
        self.assertNotContains(response,'Lazy SQL rule',status_code=409)
