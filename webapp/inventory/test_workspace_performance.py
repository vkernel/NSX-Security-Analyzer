from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from . import analysis_index, naming
from .models import (Environment, Snapshot, SnapshotRecord, SnapshotAnalysisIndex,
                     SnapshotNamingAssessment, SnapshotVMMembership, NamingPolicy, NamingProposal)
from .segmentation import build
from .workload_rules import investigate


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WorkspacePerformanceTests(TestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='Performance fixture',slug='performance-fixture',manager='offline.invalid')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),summary={},report={})
        self.user=get_user_model().objects.create_superuser('performance-admin',password='test-only')
        self.client.force_login(self.user)
        self.group=self.row('App','/groups/app',kind='group',configuration={'expression':[{'resource_type':'Condition','member_type':'VirtualMachine','key':'Tag','operator':'EQUALS','value':'Application|Portal'}]})
        self.rule=self.row('Allow app','/rules/app',configuration={'rule':{'source_groups':['/groups/app'],'destination_groups':['ANY'],'services':['ANY'],'action':'ALLOW'}})
        self.vm=self.row('VM','vm-one',view='vms',tags=[{'scope':'Application','tag':'Portal'}],vm_details={'external_id':'vm-one'})
        self.url=reverse('segmentation',args=[self.env.pk])
        self.params={'snapshot':str(self.snapshot.pk)}

    def row(self,name,path,view='inventory',**values):
        return SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=SnapshotRecord.objects.count(),view=view,name=name,sort_name=name.casefold(),
            data=dict(name=name,path=path,**values),compact={},columns=[],sort_values={},search_basic=name,search_evidence='')

    def test_projection_is_shared_and_does_not_mutate_between_requests(self):
        before=build(self.snapshot,application_mode=True)
        before['rules'][0]['configuration']['rule']['action']='DROP'
        with CaptureQueriesContext(connection) as queries:
            after=build(Snapshot.objects.get(pk=self.snapshot.pk),application_mode=True)
        self.assertEqual(after['rules'][0]['action'],'ALLOW')
        self.assertFalse(any('FROM "inventory_snapshotrecord"' in q['sql'] for q in queries))
        self.assertEqual(SnapshotAnalysisIndex.objects.count(),1)

    def test_record_changes_invalidate_projection_and_memberships(self):
        investigate(self.snapshot,self.vm,self.vm)
        self.assertTrue(SnapshotVMMembership.objects.exists())
        self.rule.data['configuration']['rule']['action']='DROP';self.rule.save()
        self.assertFalse(SnapshotAnalysisIndex.objects.exists())
        self.assertFalse(SnapshotVMMembership.objects.exists())
        self.assertEqual(build(self.snapshot,application_mode=True)['rules'][0]['action'],'DROP')

    def test_settings_and_empty_matrix_do_not_load_rule_data(self):
        with patch('inventory.analysis_index.read_rules',side_effect=AssertionError('Unneeded rule query')):
            self.assertEqual(self.client.get(self.url,dict(self.params,tab='settings')).status_code,200)
            self.assertEqual(self.client.get(self.url,dict(self.params,tab='matrix')).status_code,200)
        self.assertIsNone(SnapshotAnalysisIndex.objects.get().rules)

    def test_projection_version_upgrade_discards_dependent_caches(self):
        investigate(self.snapshot,self.vm,self.vm)
        naming.assess(self.env)
        SnapshotAnalysisIndex.objects.update(version=0)
        build(self.snapshot)
        self.assertFalse(SnapshotVMMembership.objects.exists())
        self.assertFalse(SnapshotNamingAssessment.objects.exists())

    def test_index_metadata_updates_do_not_discard_configuration_cache(self):
        build(self.snapshot)
        self.group.relationships_ready=True;self.group.save(update_fields=['relationships_ready'])
        self.assertTrue(SnapshotAnalysisIndex.objects.exists())

    def test_group_picker_is_bounded_and_retains_selected_group_outside_first_page(self):
        for i in range(65): self.row(f'Group {i:03}',f'/groups/{i}',kind='group',configuration={'expression':[]})
        response=self.client.get(self.url,dict(self.params,tab='matrix',groups='/groups/64'))
        self.assertLessEqual(response.content.count(b'data-group-option>'),51)
        self.assertContains(response,'value="/groups/64" checked')
        search=reverse('segmentation-groups',args=[self.env.pk])
        data=self.client.get(search,self.params).json()
        self.assertEqual(len(data['results']),50);self.assertTrue(data['more'])
        data=self.client.get(search,dict(self.params,q='Group 064')).json()
        self.assertEqual(data['results'][0]['path'],'/groups/64')
        other=Environment.objects.create(name='Other',slug='other-performance',manager='other-offline.invalid')
        self.assertEqual(self.client.get(reverse('segmentation-groups',args=[other.pk]),self.params).status_code,404)

    def test_status_probe_reads_only_snapshot_identity_and_index_route_redirects_once(self):
        with CaptureQueriesContext(connection) as queries:
            response=self.client.get(self.url,{'check':1})
        self.assertEqual(response.json()['latest'],str(self.snapshot.pk))
        self.assertFalse(any('"summary"' in q['sql'] or 'inventory_snapshotrecord' in q['sql'] or 'inventory_snapshotanalysisindex' in q['sql'] for q in queries))
        response=self.client.get(reverse('segmentation-index'),{'environment':self.env.pk},follow=True)
        self.assertEqual(len(response.redirect_chain),1)

    def test_memberships_are_reused_and_details_are_loaded_in_pages(self):
        investigate(self.snapshot,self.vm,self.vm)
        with patch('inventory.workload_rules.memberships',side_effect=AssertionError('Membership recalculated')):
            result=investigate(self.snapshot,self.vm,self.vm)
        self.assertEqual(result['source']['groups'][0]['path'],'/groups/app')
        url=reverse('segmentation-pair',args=[self.env.pk])
        response=self.client.get(url,dict(self.params,source='vm-one',destination='vm-one'))
        self.assertContains(response,'data-membership-page-url')
        self.assertNotContains(response,'data-membership-check')
        response=self.client.get(reverse('segmentation-memberships',args=[self.env.pk]),dict(self.params,vm='vm-one'))
        self.assertContains(response,'1–1 of 1 group relationships')
        self.assertContains(response,'data-membership-check')
        self.assertNotContains(response,'<html')

    def test_naming_reuses_assessment_but_policy_proposal_and_date_changes_refresh_it(self):
        config={'target':'group','group_type':'all','separator':'-','max_length':63,'components':[{'field':'Application','source':'tag','scope':'Application','required':True,'case':'upper','format':'extended','max_length':32}]}
        policy=NamingPolicy.objects.create(name='Apps',configuration=config,draft=config,enabled=True)
        first=naming.assess(self.env)[1][0]
        with patch('inventory.naming.evaluate',side_effect=AssertionError('Assessment recomputed')):
            self.assertEqual(naming.assess(self.env)[1][0]['proposed'],'PORTAL')
        policy.configuration['components'][0]['case']='lower';policy.save()
        self.assertEqual(naming.assess(self.env)[1][0]['proposed'],'portal')
        proposal=NamingProposal.objects.create(environment=self.env,group_path='/groups/app',baseline=first['baseline'],state='reviewed',tags={'Application':'Changed'},author=self.user)
        self.assertEqual(naming.assess(self.env)[1][0]['proposed'],'changed')
        proposal.state='exception';proposal.tags={};proposal.owner=self.user;proposal.exception_policy_stamp=naming.assess(self.env)[1][0]['policy_stamp'];proposal.expires_at=timezone.localdate();proposal.save()
        self.assertEqual(naming.assess(self.env)[1][0]['status'],'Reviewed exception')
        with patch('inventory.naming.timezone.localdate',return_value=timezone.localdate()+timedelta(days=1)):
            self.assertNotEqual(naming.assess(self.env)[1][0]['status'],'Reviewed exception')

    def test_archiving_and_rebuilding_discard_derived_data(self):
        build(self.snapshot);naming.assess(self.env)
        self.assertTrue(SnapshotNamingAssessment.objects.exists())
        analysis_index.invalidate(self.snapshot.pk)
        self.assertFalse(SnapshotAnalysisIndex.objects.exists())
        self.assertFalse(SnapshotNamingAssessment.objects.exists())
        self.snapshot.browsing_status='archived';self.snapshot.save(update_fields=['browsing_status'])
        self.assertEqual(analysis_index.load(self.snapshot,'groups')['groups'],[])

    def test_automatic_updates_are_fragments_and_preserve_authentication(self):
        for route,args,region in [('findings',[self.env.pk],'review-results'),('activity',[],'activity-results'),('my-work',[],'review-results'),('finding-policy',[],'recalculation-results'),('snapshot-comparison',[self.env.pk],'comparison-results')]:
            with self.subTest(route=route):
                response=self.client.get(reverse(route,args=args),HTTP_X_REQUESTED_WITH='task-status')
                self.assertContains(response,'id="'+region+'"')
                self.assertNotContains(response,'<html')
                self.assertIn('X-Requested-With',response['Vary'])
        self.client.logout()
        self.assertEqual(self.client.get(reverse('findings',args=[self.env.pk]),HTTP_X_REQUESTED_WITH='task-status').status_code,302)

    def test_timing_header_exposes_durations_without_queries_or_values(self):
        response=self.client.get(self.url,self.params)
        self.assertIn('db;dur=',response['Server-Timing'])
        self.assertIn('analysis;dur=',response['Server-Timing'])
        self.assertIn('render;dur=',response['Server-Timing'])
        self.assertNotIn('SELECT',response['Server-Timing'])
