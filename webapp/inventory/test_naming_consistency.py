"""Synthetic name outcomes, review progress and evidence quality stay independent."""
from copy import deepcopy
from datetime import timedelta
from urllib.parse import parse_qs,urlparse
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from . import naming
from .models import (Environment,Snapshot,SnapshotRecord,NamingPolicy,NamingProposal,
                     NamingScopeBinding,SnapshotNamingAssessment)
from .naming_views import impact


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class NamingConsistencyTests(TestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='Synthetic naming',slug='naming-consistency',manager='https://naming.invalid')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),summary={},report={})
        self.config={'target':'group','group_type':'static','separator':'-','max_length':63,'match_field':'','match_value':'',
            'components':[{'field':'Suffix','source':'identity','scope':'','literal':'','required':True,
                           'case':'keep','format':'extended','max_length':32,'mapping':{}}]}
        self.policy=NamingPolicy.objects.create(name='Synthetic policy',environment=self.env,enabled=True,
            configuration=deepcopy(self.config),draft=deepcopy(self.config),revision=1)
        self.group=self.add_group('EXPECTED')
        self.user=get_user_model().objects.create_superuser('naming-consistency-admin')
        self.client.force_login(self.user)
        self.url=reverse('naming-compliance',args=[self.env.pk])

    def add_group(self,suffix,name=None):
        return SnapshotRecord.objects.create(snapshot=self.snapshot,view='inventory',ordinal=SnapshotRecord.objects.count(),
            name=name or suffix,compact={},columns=[],sort_values={},data={'path':'/infra/domains/default/groups/'+suffix,
                'kind':'group','configuration':{'expression':[{'resource_type':'IPAddressExpression','ip_addresses':['192.0.2.1']}]}})

    def row(self):
        return naming.assess(self.env)[1][0]

    def workflow(self):
        row=self.row()
        return NamingProposal.objects.create(environment=self.env,group_path=row['identity'],baseline=row['baseline'],owner=self.user,
            workflow={'state':'in_review','policy_stamp':row['policy_stamp'],'definition_stamp':row['definition_stamp']})

    def test_draft_save_and_unrelated_active_rules_do_not_invalidate_review(self):
        self.workflow();stamp=self.row()['policy_stamp']
        self.policy.draft['components'][0]['case']='lower';self.policy.revision+=1;self.policy.save()
        unrelated=NamingPolicy.objects.create(name='Unrelated dynamic',environment=self.env,enabled=True,
            configuration=dict(deepcopy(self.config),group_type='dynamic'))
        NamingScopeBinding.objects.create(policy=unrelated,environment=self.env,scopes={'Other':'Other scope'})
        row=self.row()
        self.assertEqual(row['policy_stamp'],stamp);self.assertFalse(row['review_required'])
        self.assertEqual(row['status'],'Compliant');self.assertEqual(naming.queue_counts([row])['compliant'],1)
        NamingScopeBinding.objects.create(policy=self.policy,environment=self.env,scopes={},draft_scopes={'Suffix':'Unused draft'})
        self.assertEqual(self.row()['policy_stamp'],stamp)

    def test_relevant_active_change_requires_review_without_losing_name_compliance(self):
        self.workflow()
        self.policy.configuration['components'][0]['max_length']=33;self.policy.save()
        row=self.row()
        self.assertTrue(row['work_stale']);self.assertTrue(row['review_required'])
        self.assertFalse(row['needs_attention']);self.assertEqual(row['status'],'Compliant')
        self.assertEqual(naming.queue_counts([row]),{'all':1,'compliant':1,'attention':0,'exceptions':0})
        compliant=self.client.get(self.url,{'tab':'compliant'})
        review=self.client.get(self.url,{'tab':'review'})
        self.assertEqual(compliant.context['page'].paginator.count,1)
        self.assertEqual(review.context['page'].paginator.count,1)
        self.assertContains(compliant,'Review required')
        self.assertContains(review,'Review needed')

    def test_draft_revision_keeps_a_current_active_assessment_cache_valid(self):
        self.workflow();self.row()
        self.policy.draft['max_length']=64;self.policy.revision+=1;self.policy.save()
        with patch('inventory.naming.evaluate',side_effect=AssertionError('Draft edit invalidated active assessment')):
            row=self.row()
        self.assertEqual(row['status'],'Compliant');self.assertFalse(row['review_required'])

    def test_only_effective_active_tag_scope_changes_review(self):
        self.policy.configuration['group_type']='dynamic'
        self.policy.configuration['components'][0].update(source='tag',scope='App')
        self.policy.save()
        self.group.name='EXPECTED'
        self.group.data['configuration']={'expression':[{'resource_type':'Condition','member_type':'VirtualMachine',
            'key':'Tag','operator':'EQUALS','value':'App|EXPECTED'}]}
        self.group.save()
        self.workflow();stamp=self.row()['policy_stamp']
        binding=NamingScopeBinding.objects.create(policy=self.policy,environment=self.env,scopes={},draft_scopes={'Suffix':'Other'})
        self.assertEqual(self.row()['policy_stamp'],stamp)
        binding.scopes={'Suffix':'Other'};binding.save()
        self.assertTrue(self.row()['review_required'])
        self.assertEqual(self.row()['status'],'Missing required tags')

    def test_exception_survives_drafts_but_not_relevant_active_rule_changes(self):
        row=self.row()
        NamingProposal.objects.create(environment=self.env,group_path=row['identity'],baseline=row['baseline'],state='exception',
            owner=self.user,expires_at=timezone.localdate()+timedelta(days=7),note='Reviewed exception',exception_policy_stamp=row['policy_stamp'])
        self.policy.revision+=1;self.policy.draft['max_length']=64;self.policy.save()
        self.assertEqual(self.row()['status'],'Reviewed exception');self.assertFalse(self.row()['exception_stale'])
        self.policy.configuration['max_length']=64;self.policy.save()
        row=self.row();self.assertTrue(row['exception_stale']);self.assertTrue(row['review_required'])
        self.assertEqual(row['status'],'Compliant') # Name still matches; exception review is separate.

    def test_unchanged_legacy_review_is_recognized_conservatively(self):
        proposal=self.workflow()
        proposal.workflow['policy_stamp']=naming.digest([[(self.policy.pk,self.policy.revision,self.policy.configuration)],{}])
        proposal.save()
        self.assertFalse(self.row()['review_required'])
        self.policy.configuration['max_length']=64;self.policy.save()
        self.assertTrue(self.row()['review_required'])

    def test_coverage_warning_preserves_name_matches_as_explicitly_provisional(self):
        self.workflow()
        self.snapshot.summary={'inventory_warnings':['Synthetic warning']};self.snapshot.save(update_fields=['summary'])
        row=self.row()
        self.assertEqual(row['status'],'Compliant');self.assertEqual(row['coverage_status'],'provisional')
        self.assertEqual(naming.counts([row]),[('Name matches (provisional)',1)])
        response=self.client.get(self.url,{'tab':'compliant'})
        self.assertEqual(response.context['tab_counts']['compliant'],1)
        self.assertContains(response,'Name matches (provisional)')
        self.assertNotContains(response,'No compliant groups')
        csv=self.client.get(self.url,{'tab':'compliant','export':'csv'}).content.decode()
        self.assertIn('Name matches (provisional)',csv);self.assertIn('evidence_quality',csv)
        self.snapshot.summary={};self.snapshot.save(update_fields=['summary'])
        self.assertEqual(self.row()['outcome_label'],'Compliant')

    def test_unavailable_assessment_shows_dashes_instead_of_zero_compliance(self):
        self.snapshot.browsing_status='archived';self.snapshot.save(update_fields=['browsing_status'])
        response=self.client.get(self.url,{'tab':'compliant'})
        self.assertEqual(response.context['assessment_state'],'unavailable')
        self.assertTrue(all(item['count'] is None for item in response.context['naming_navigation']))
        self.assertContains(response,'Assessment unavailable')
        self.assertNotContains(response,'No compliant groups match these filters')
        self.assertNotContains(response,'0 matching groups')
        self.assertNotContains(response,'0 matching results')
        self.assertNotContains(response,'table class="naming-results"')
        self.assertEqual(self.client.get(self.url,{'export':'csv'}).status_code,409)
        summary=impact(self.policy)['summaries'][0]
        self.assertEqual(summary['assessment_state'],'unavailable')

    def test_draft_preview_and_active_page_use_same_name_outcome_counts(self):
        self.workflow()
        self.policy.configuration['max_length']=64;self.policy.save()
        self.policy.draft=deepcopy(self.policy.configuration);self.policy.save()
        active=self.client.get(self.url,{'tab':'all'})
        summary=impact(self.policy)['summaries'][0]
        self.assertEqual(summary['policy_counts'],active.context['tab_counts'])
        self.assertEqual(summary['review_count'],1)
        self.assertEqual(summary['compliant_label'],'Name matches (draft)')
        self.assertEqual(summary['counts'],[('Name matches (draft)',1)])
        response=self.client.post(reverse('naming-policy-edit',args=[self.policy.pk]),{'action':'preview'})
        self.assertContains(response,'Name matches (draft): 1')
        self.assertContains(response,'Source collection:')

    def test_switching_queues_clears_outcome_but_preserves_broad_filters(self):
        self.add_group('SECOND','WRONG')
        response=self.client.get(self.url,{'tab':'attention','status':'Name differs','policy':self.policy.pk,'q':'/infra/','group_type':'static'})
        for item in response.context['naming_navigation']:
            params=parse_qs(urlparse(item['url']).query)
            if item['key']!='attention':self.assertNotIn('status',params)
            self.assertEqual(params['policy'],[str(self.policy.pk)])
            self.assertEqual(params['q'],['/infra/']);self.assertEqual(params['group_type'],['static'])
            destination=self.client.get(self.url+item['url'])
            self.assertEqual(destination.context['page'].paginator.count,item['count'])

    def test_warm_and_cold_assessments_preserve_the_same_qualification_and_counts(self):
        self.workflow()
        self.snapshot.summary={'inventory_warnings':True};self.snapshot.save(update_fields=['summary'])
        first=naming.assess(self.env)[1]
        with patch('inventory.naming.evaluate',side_effect=AssertionError('Warm cache recomputed')):
            warm=naming.assess(self.env)[1]
        SnapshotNamingAssessment.objects.filter(snapshot=self.snapshot).delete()
        cold=naming.assess(self.env)[1]
        for rows in (warm,cold):
            self.assertEqual(naming.queue_counts(rows),naming.queue_counts(first))
            self.assertEqual(rows[0]['coverage_status'],first[0]['coverage_status'])
            self.assertEqual(rows[0]['policy_stamp'],first[0]['policy_stamp'])
