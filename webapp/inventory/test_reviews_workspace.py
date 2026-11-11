"""Shared queue semantics and a single paginated table for findings and cleanup."""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit, parse_qs
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, Finding, FindingEvent, DeletionBatch, DeletionItem


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ReviewsWorkspaceTests(TestCase):
    def setUp(self):
        users=get_user_model()
        self.admin=users.objects.create_superuser('review-admin',password='synthetic-only')
        self.other=users.objects.create_user('review-other',is_staff=True)
        self.viewer=users.objects.create_user('review-viewer')
        self.env=Environment.objects.create(name='East',slug='reviews-east',manager='https://reviews-east.invalid')
        self.west=Environment.objects.create(name='West',slug='reviews-west',manager='https://reviews-west.invalid')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={})
        self.client.force_login(self.admin)
        self.mine=self.finding('My finding',owner=self.admin,state='owner_review')
        self.finding('Other review',owner=self.other,state='owner_review')
        self.finding('Unassigned')
        self.finding('Independent approval',owner=self.other,state='second_review',approvals={'owner':{'actor_id':str(self.other.pk)}})
        self.finding('My first approval',owner=self.other,state='second_review',approvals={'owner':{'actor_id':str(self.admin.pk)}})
        self.finding('Ready finding',owner=self.other,state='ready')
        self.finding('Rejected finding',owner=self.other,state='rejected')
        self.finding('Completed finding',owner=self.other,state='decommissioned')
        self.finding('Suppressed finding',qualification='suppressed')
        self.finding('West finding',environment=self.west,owner=self.admin,state='owner_review')
        self.url=reverse('my-work')

    def finding(self,name,environment=None,owner=None,state='unassigned',qualification='eligible',approvals=None):
        now=timezone.now()
        return Finding.objects.create(environment=environment or self.env,snapshot=self.snapshot,name=name,
            path='/infra/domains/default/groups/'+name.replace(' ','-'),kind='empty_group',fingerprint=name,
            owner=owner,workflow_state=state,qualification=qualification,approvals=approvals or {},
            first_seen=now,last_seen=now,evaluated_at=now)

    def cleanup(self,name='Cleanup task',requester=None,approvals=None):
        batch=DeletionBatch.objects.create(environment=self.env,requested_by=requester or self.admin,
            manager=self.env.manager,status='preview_ready')
        return DeletionItem.objects.create(batch=batch,finding=self.mine,name=name,path='/infra/domains/default/groups/cleanup',
            revision=0,status='cleanup_review',cleanup_plan=[{'action':'update_rule','path':'/infra/domains/default/security-policies/p/rules/r','name':'Rule','policy_name':'Policy'}],
            cleanup_approvals=approvals or [])

    def test_environment_and_global_entry_use_same_table_queues_and_counts(self):
        for queue in ('assigned','second','unassigned','ready','all'):
            a=self.client.get(self.url,{'queue':queue,'environment':self.env.pk,'sort':'name'})
            b=self.client.get(reverse('findings',args=[self.env.pk]),{'queue':queue,'sort':'name'})
            self.assertEqual(a.status_code,200)
            self.assertEqual(a.context['queue_counts'],b.context['queue_counts'])
            self.assertEqual([(r.review_kind,r.pk) for r in a.context['page']],[(r.review_kind,r.pk) for r in b.context['page']])
            self.assertTemplateUsed(a,'inventory/reviews.html')
            self.assertTemplateUsed(b,'inventory/reviews.html')
            self.assertContains(a,'class="review-task-table"',count=1)
        self.assertEqual(a.context['queue_counts'],{'assigned':1,'second':1,'unassigned':1,'ready':1,'all':8})

    def test_scope_preserved_and_stale_stage_filters_clear_on_queue_changes(self):
        response=self.client.get(self.url,{'queue':'all','environment':self.env.pk,'review':'rejected','owner':'me','q':'finding'})
        for entry in response.context['queue_links']:
            query=parse_qs(urlsplit(entry['url']).query)
            self.assertEqual(query['environment'],[str(self.env.pk)])
            self.assertNotIn('review',query);self.assertNotIn('owner',query)
            self.assertEqual(query['q'],['finding'])
        response=self.client.get(self.url,{'queue':'second','environment':self.env.pk,'review':'rejected','owner':'me'})
        self.assertEqual([row.name for row in response.context['page']],['Independent approval'])
        for scope in ('invalid','99999999999999999999','999'):
            self.assertEqual(self.client.get(self.url,{'environment':scope}).context['page'].paginator.count,0)

    def test_cleanup_is_in_the_same_sorted_paginated_table_with_distinct_actions(self):
        cleanup=self.cleanup()
        response=self.client.get(self.url,{'queue':'assigned','environment':self.env.pk,'sort':'name','page_size':25})
        self.assertEqual([r.review_kind for r in response.context['page']],['cleanup','finding'])
        self.assertEqual(response.context['page'].paginator.count,2)
        self.assertContains(response,'Firewall reference cleanup')
        self.assertNotContains(response,'class="cleanup-task-table"')
        self.assertContains(response,'data-bulk-choice="bulk-work"')
        self.assertContains(response,'data-bulk-choice="bulk-cleanup"')
        response=self.client.get(self.url,{'queue':'all','task_type':'cleanup'})
        self.assertEqual([r.pk for r in response.context['page']],[cleanup.pk])

    def test_operator_and_viewer_permissions_are_preserved(self):
        self.cleanup()
        self.client.force_login(self.other)
        response=self.client.get(self.url,{'queue':'all'})
        self.assertNotContains(response,'data-review-task="cleanup"')
        self.assertNotContains(response,'data-bulk-action="preview"')
        self.client.force_login(self.viewer)
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['queue'],'all')
        self.assertNotContains(response,'data-bulk-choice=')
        self.assertNotContains(response,'data-user-search=')
        self.assertNotContains(response,'Provide second approval')
        self.client.logout();self.assertEqual(self.client.get(self.url).status_code,302)

    def test_explicit_next_actions_and_history_filters(self):
        response=self.client.get(self.url,{'queue':'all','environment':self.env.pk})
        actions={r.name:r.next_action for r in response.context['page']}
        self.assertEqual(actions['My finding'],'Review evidence')
        self.assertEqual(actions['Independent approval'],'Provide second approval')
        self.assertEqual(actions['Unassigned'],'Assign owner')
        Finding.objects.filter(name='Ready finding').update(qualification='insufficient')
        changed=self.client.get(self.url,{'queue':'ready','environment':self.env.pk}).context['page'][0]
        self.assertEqual(changed.next_action,'Review changed evidence')
        self.assertFalse(changed.can_delete)
        for status,name in [('rejected','Rejected finding'),('decommissioned','Completed finding'),('suppressed','Suppressed finding')]:
            response=self.client.get(self.url,{'queue':'all','environment':self.env.pk,'review':status})
            self.assertEqual([r.name for r in response.context['page']],[name])

    def test_bulk_assign_owner_preserves_revision_and_existing_owner(self):
        item=Finding.objects.get(name='Unassigned')
        url=reverse('bulk-actions',args=['findings'])
        response=self.client.post(url,{'action':'assign','selected':[f'{item.pk}:{item.revision}'],
            'return_to':self.url+'?queue=unassigned','confirm':'yes','owner':self.other.pk})
        self.assertContains(response,'1 succeeded')
        item.refresh_from_db();self.assertEqual(item.owner,self.other)
        response=self.client.post(url,{'action':'assign','selected':[f'{item.pk}:{item.revision}'],
            'confirm':'yes','owner':self.admin.pk})
        self.assertContains(response,'Already assigned')
        item.refresh_from_db();self.assertEqual(item.owner,self.other)

    def test_return_links_and_adjacent_navigation_cover_shared_workspace(self):
        back=self.url+'?'+urlencode({'queue':'all','environment':self.env.pk,'sort':'name','q':'finding'})
        response=self.client.get(reverse('finding-detail',args=[self.env.pk,self.mine.pk]),{'return_to':back})
        self.assertEqual(response.context['review_return'],back)
        self.assertContains(response,'← Reviews')
        self.assertTrue(response.context['review_navigation'])
        cleanup=self.cleanup()
        response=self.client.get(reverse('deletion-reference-cleanup',args=[cleanup.pk]),{'return_to':back})
        self.assertEqual(response.context['review_return'],back)
        self.assertContains(response,'← Reviews')

    def test_large_queue_uses_database_pagination_without_reading_snapshot_reports(self):
        now=timezone.now()
        Finding.objects.bulk_create([Finding(environment=self.env,name=f'Large {i:05}',path=f'/infra/groups/large-{i}',kind='unused',
            fingerprint=str(i),owner=self.admin,workflow_state='owner_review',qualification='eligible',
            first_seen=now,last_seen=now,evaluated_at=now) for i in range(3000)])
        with CaptureQueriesContext(connection) as queries:
            response=self.client.get(self.url,{'queue':'assigned','environment':self.env.pk,'page':50,'page_size':25})
        self.assertEqual(len(response.context['page']),25)
        self.assertEqual(response.context['page'].paginator.count,3001)
        self.assertTrue(any('UNION' in row['sql'] and 'LIMIT 25 OFFSET 1225' in row['sql'] for row in queries))
        self.assertFalse(any('"report"' in row['sql'] or '"inventory_finding"."evidence"' in row['sql'] for row in queries))
        self.assertLess(len(queries),35)
