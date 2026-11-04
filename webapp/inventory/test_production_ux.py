"""Context integrity, navigation and task-specific presentation contracts."""
import re
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Environment, NamingPolicy, SegmentationSettings, Snapshot
from .services import prepare_snapshot
from .tests import sample_report


@override_settings(STORAGES={'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ProductionUXTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('ux-viewer')
        self.env=Environment.objects.create(name='East',slug='ux-east',manager='https://offline.invalid')
        self.other=Environment.objects.create(name='West',slug='ux-west',manager='https://west.offline.invalid')
        report=sample_report();report['manager']=self.env.manager
        self.snapshot=prepare_snapshot(self.env,report);self.snapshot.save()
        report=sample_report();report['manager']=self.other.manager
        self.other_snapshot=prepare_snapshot(self.other,report);self.other_snapshot.save()
        self.client.force_login(self.user)

    def test_environment_targets_resolve_latest_snapshot_without_other_environment_ids(self):
        response=self.client.get(reverse('snapshot',args=[self.snapshot.pk]), {'browse':'1','source':'old-vm','groups':'old-group'})
        option=re.search(r'<option[^>]+value="'+str(self.other.pk)+r'"[^>]+>',response.content.decode()).group()
        self.assertIn(str(self.other_snapshot.pk),option)
        self.assertNotIn('old-vm',option);self.assertNotIn('old-group',option)
        self.assertIn('context_changed=1',option)
        self.assertContains(response,'data-snapshot-choice')
        self.assertContains(response,'id="collection-details-template"')
        self.assertNotContains(response,'class="relay-theme-menu"')

    def test_pinned_assessment_source_has_no_snapshot_switch_action(self):
        row={'id':1,'identity':'/infra/domains/default/groups/one','name':'One','status':'Compliant','needs_attention':False,'group_type':'dynamic','observed_tags':{},'matching_policy_ids':[],'next_action':'Review','name_diff':[]}
        with patch('inventory.naming_views.analyze',return_value=(self.snapshot,[row],'')):
            response=self.client.get(reverse('naming-compliance',args=[self.env.pk]), {'tab':'compliant'})
        self.assertContains(response,'Pinned evidence')
        self.assertNotContains(response,'data-snapshot-open')
        self.assertNotContains(response,'Fix recurring issues')
        table=re.search(r'<table class="naming-results">(.*?)</table>',response.content.decode(),re.S).group()
        self.assertNotIn('Unassigned',table)
        self.assertNotContains(response,'new policy may have changed')

    def test_issue_counts_match_filtered_attention_and_viewers_have_no_policy_edit_actions(self):
        policy=NamingPolicy.objects.create(name='Groups',enabled=True,configuration={})
        rows=[{'id':i,'identity':f'/groups/{i}','name':name,'status':'Missing required tags','needs_attention':True,'group_type':'dynamic','observed_tags':{},'matching_policy_ids':[str(policy.pk)],'next_action':'Review','name_diff':[]} for i,name in enumerate(['Portal','Database'],1)]
        with patch('inventory.naming_views.analyze',return_value=(self.snapshot,rows,'')):
            response=self.client.get(reverse('naming-compliance',args=[self.env.pk]),{'q':'Portal'})
        self.assertEqual(response.context['issue_groups'][0]['count'],1)
        self.assertIn('q=Portal',response.context['issue_groups'][0]['url'])
        self.assertContains(response,'In these filtered results')
        self.assertNotContains(response,reverse('naming-policy-edit',args=[policy.pk]))

    def test_canonical_segmentation_navigation_is_rendered_without_client_cloning(self):
        SegmentationSettings.objects.create(environment=self.env,grouping_scope='Application')
        response=self.client.get(reverse('segmentation',args=[self.env.pk]),{'snapshot':str(self.snapshot.pk)})
        text=response.content.decode();panel=text.split('data-section-links>',1)[1].split('</div>',1)[0]
        self.assertIn('Security matrix',panel)
        self.assertIn('data-section-navigation',panel)

    def test_naming_policy_filter_counts_and_queue_links_use_same_scope(self):
        from urllib.parse import parse_qs, urlparse
        policies=[NamingPolicy.objects.create(name=name,environment=self.env,enabled=True,configuration={}) for name in ('Hosts','Applications')]
        rows=[{'id':i,'identity':f'/groups/{i}','name':f'Group {i}','status':status,'needs_attention':status!='Compliant',
               'group_type':'static','observed_tags':{},'matching_policy_ids':[str(policy.pk)],'next_action':'Review','name_diff':[]}
              for i,(policy,status) in enumerate([(policies[0],'Compliant'),(policies[0],'Name differs'),(policies[1],'Compliant')],1)]
        with patch('inventory.naming_views.analyze',return_value=(self.snapshot,rows,'')):
            response=self.client.get(reverse('naming-compliance',args=[self.env.pk]), {'policy':policies[0].pk,'q':'Group','group_type':'static','tab':'compliant','page':5})
        self.assertEqual(response.context['tab_counts'],{'all':2,'compliant':1,'attention':1,'exceptions':0})
        self.assertEqual(response.context['page'].paginator.count,1)
        for item in response.context['naming_navigation']:
            params=parse_qs(urlparse(item['url']).query)
            self.assertEqual(params['policy'],[str(policies[0].pk)])
            self.assertEqual(params['q'],['Group']);self.assertEqual(params['group_type'],['static'])
            self.assertNotIn('page',params)
            with patch('inventory.naming_views.analyze',return_value=(self.snapshot,rows,'')):
                destination=self.client.get(reverse('naming-compliance',args=[self.env.pk])+item['url'])
            self.assertEqual(destination.context['page'].paginator.count,item['count'])

    def test_attention_issue_buckets_cover_all_rows_and_counts_link_to_exact_results(self):
        policy=NamingPolicy.objects.create(name='Groups',enabled=True,configuration={})
        statuses=['No applicable policy','Invalid value','Missing required tags','Name differs','Definition review required','Evidence incomplete','Compliant','Reviewed exception']
        rows=[{'id':i,'identity':f'/groups/{i}','name':f'Group {i}','status':status,'needs_attention':True,
               'group_type':'static','observed_tags':{},'matching_policy_ids':[] if i==0 else [str(policy.pk)],'next_action':'Review','name_diff':[]}
              for i,status in enumerate(statuses)]
        url=reverse('naming-compliance',args=[self.env.pk])
        with patch('inventory.naming_views.analyze',return_value=(self.snapshot,rows,'')):
            response=self.client.get(url)
        self.assertEqual(response.context['tab_counts'],{'all':8,'compliant':0,'attention':8,'exceptions':0})
        self.assertEqual(sum(bucket['count'] for bucket in response.context['issue_groups']),8)
        for bucket in response.context['issue_groups']:
            with patch('inventory.naming_views.analyze',return_value=(self.snapshot,rows,'')):
                destination=self.client.get(url+bucket['url'])
            self.assertEqual(destination.context['page'].paginator.count,bucket['count'])
            for entry in bucket['policies']:
                with patch('inventory.naming_views.analyze',return_value=(self.snapshot,rows,'')):
                    destination=self.client.get(url+entry['url'])
                self.assertEqual(destination.context['page'].paginator.count,entry['count'])

    def test_policy_filtered_conflict_issue_links_do_not_expand_to_another_policy(self):
        policies=[NamingPolicy.objects.create(name=f'Conflict {i}',enabled=True,configuration={}) for i in range(2)]
        rows=[{'id':i,'identity':f'/groups/{i}','name':f'Group {i}','status':'Ambiguous policy','needs_attention':True,
               'group_type':'static','observed_tags':{},'matching_policy_ids':ids,'next_action':'Review','name_diff':[]}
              for i,ids in enumerate([[str(p.pk) for p in policies],[str(policies[1].pk)]])]
        url=reverse('naming-compliance',args=[self.env.pk])
        with patch('inventory.naming_views.analyze',return_value=(self.snapshot,rows,'')):
            response=self.client.get(url,{'policy':policies[0].pk})
        issue=response.context['issue_groups'][0]
        self.assertEqual(issue['count'],1)
        self.assertEqual([p['name'] for p in issue['policies']],[policies[0].name])
        with patch('inventory.naming_views.analyze',return_value=(self.snapshot,rows,'')):
            destination=self.client.get(url+issue['policies'][0]['url'])
        self.assertEqual(destination.context['page'].paginator.count,1)

    def test_history_keeps_selected_evidence_beyond_recent_limit(self):
        from datetime import timedelta
        from .templatetags.workspace_shell import snapshot_context
        from django.test import RequestFactory
        from django.urls import resolve
        Snapshot.objects.bulk_create([
            Snapshot(environment=self.env, generated_at=self.snapshot.generated_at + timedelta(minutes=i+1), report={})
            for i in range(51)
        ])
        request=RequestFactory().get(reverse('snapshot', args=[self.snapshot.pk]))
        request.resolver_match=resolve(request.path)
        result=snapshot_context({'request':request,'snapshot':self.snapshot})
        self.assertEqual(len(result['snapshot_choices']),51)
        self.assertEqual(sum(choice['selected'] for choice in result['snapshot_choices']),1)
        self.assertEqual(result['snapshot_choices'][-1]['snapshot'].pk,self.snapshot.pk)
        self.assertFalse(result['snapshot_is_latest'])

    def test_recurring_issues_include_policy_counts_and_safe_filtered_links(self):
        policies=[NamingPolicy.objects.create(name=f'Policy {i}',enabled=True,configuration={}) for i in range(4)]
        row={'id':1,'identity':'/groups/portal','name':'Portal','status':'Ambiguous policy','needs_attention':True,'group_type':'dynamic','observed_tags':{},'matching_policy_ids':[str(p.pk) for p in policies],'next_action':'Review','name_diff':[]}
        with patch('inventory.naming_views.analyze',return_value=(self.snapshot,[row],'')):
            response=self.client.get(reverse('naming-compliance',args=[self.env.pk]),{'q':'Portal'})
        issue=response.context['issue_groups'][0]
        self.assertEqual(issue['count'],1)
        self.assertEqual(len(issue['policies']),3)
        self.assertEqual(len(issue['more_policies']),1)
        for entry in issue['policies']+issue['more_policies']:
            self.assertEqual(entry['count'],1)
            self.assertIn('q=Portal',entry['url'])
            self.assertIn('status=Ambiguous+policy',entry['url'])
            self.assertIn('policy=',entry['url'])
        self.assertContains(response,'A group can match more than one policy.')
        for policy in policies:
            self.assertContains(response,policy.name)
            self.assertNotContains(response,reverse('naming-policy-edit',args=[policy.pk]))

    def test_recent_environment_shortcuts_are_bounded_pruned_and_session_scoped(self):
        from django.test import Client
        east=reverse('naming-compliance',args=[self.env.pk])
        west=reverse('naming-compliance',args=[self.other.pk])
        with patch('inventory.naming_views.analyze',return_value=(None,[],'')):
            self.client.get(east)
            self.client.get(west)
            response=self.client.get(east)
            self.assertRegex(response.content.decode(),r'value="'+str(self.other.pk)+r'"[^>]*data-recent-rank="2"')
            session=self.client.session
            session['recent_environment_ids']=['missing',str(self.other.pk),str(self.other.pk)]
            session.save()
            self.client.get(east)
            self.assertEqual(self.client.session['recent_environment_ids'],[str(self.env.pk),str(self.other.pk)])
            extra=Environment.objects.bulk_create([Environment(name=f'Recent {i}',slug=f'recent-{i}',manager=f'https://recent-{i}.invalid') for i in range(6)])
            for env in extra:
                self.client.get(reverse('naming-compliance',args=[env.pk]))
            self.assertEqual(len(self.client.session['recent_environment_ids']),5)
            other_client=Client();other_client.force_login(get_user_model().objects.create_user('another-ux-viewer'))
            other_response=other_client.get(east)
            self.assertNotContains(other_response,'data-recent-rank=')
            self.assertEqual(other_client.session['recent_environment_ids'],[str(self.env.pk)])

    def test_policy_editor_and_scope_mapping_have_distinct_label_targets(self):
        self.user.is_superuser=True;self.user.is_staff=True;self.user.save()
        policy=NamingPolicy.objects.create(name='Groups',enabled=True,configuration={},draft={})
        response=self.client.get(reverse('naming-policy-edit',args=[policy.pk]))
        self.assertContains(response,'id="id_environment"',count=1)
        self.assertContains(response,'id="id_mapping_environment"',count=1)
        self.assertContains(response,'for="id_mapping_environment"')
