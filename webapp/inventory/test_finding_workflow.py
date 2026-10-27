from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Finding, Snapshot, SnapshotCoverage, AuditEvent, SnapshotRecord
from .finding_workflow import decide, invalidate, FIELDS


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class FindingWorkflowTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user('owner', is_staff=True)
        self.reviewer = users.create_user('reviewer', is_staff=True)
        self.admin = users.create_superuser('admin-review', password='test-only')
        self.viewer = users.create_user('viewer')
        self.env = Environment.objects.create(name='Review', slug='review', manager='https://nsx.example')
        self.snapshot = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report={})
        SnapshotCoverage.objects.create(snapshot=self.snapshot)
        now=timezone.now()
        self.finding = Finding.objects.create(environment=self.env, kind='empty_group', path='/infra/groups/a',
            name='A', fingerprint='a', evidence={'membership':'empty'}, first_seen=now, last_seen=now,
            evaluated_at=now, snapshot=self.snapshot, qualification='eligible')
        self.record = SnapshotRecord.objects.create(snapshot=self.snapshot, ordinal=1, view='inventory', name='A', sort_name='a', compact={'path':self.finding.path,'membership':'empty'}, data={}, columns=[], sort_values={}, search_basic='', search_evidence='')
        self.url=reverse('finding-detail',args=[self.env.pk,self.finding.pk])

    def test_changed_impact_preview_requires_refresh_before_approval(self):
        from unittest.mock import patch
        self.client.force_login(self.owner)
        session = self.client.session
        session['impact_preview_' + str(self.finding.pk)] = 'older-snapshot'
        session.save()
        with patch('inventory.finding_workflow.decide') as decide_mock:
            response = self.client.post(self.url, {'revision':self.finding.revision,
                'action':'approve', 'note':'Reviewed dependencies'})
        self.assertContains(response, 'snapshot changed since your impact preview')
        decide_mock.assert_not_called()

    def assign(self, owner=None):
        decide(self.finding, self.admin, 'assign', 'Please check dependencies.', owner or self.owner)

    def test_release_preserves_approval_and_resume_prevents_self_approval(self):
        self.approve_owner()
        approvals = self.finding.approvals.copy()
        revision = self.finding.revision
        decide(self.finding, self.owner, 'unassign', 'Unavailable this week')
        self.assertIsNone(self.finding.owner_id)
        self.assertEqual(self.finding.approvals, approvals)
        self.assertEqual(self.finding.workflow_state, 'second_review')
        self.assertEqual(self.finding.revision, revision + 1)
        event = self.finding.events.get(details__action='unassign')
        self.assertEqual(event.details['previous_owner_id'], self.owner.pk)
        with self.assertRaises(ValidationError):
            decide(self.finding, self.reviewer, 'approve', 'Checked')
        decide(self.finding, self.admin, 'assign', 'New owner', self.reviewer)
        self.assertEqual(self.finding.approvals, approvals)
        with self.assertRaises(ValidationError):
            decide(self.finding, self.owner, 'approve', 'Cannot approve twice')

    def test_release_permissions_reason_and_admin_override(self):
        self.assign()
        for actor, reason in ((self.reviewer, 'Not mine'), (self.viewer, 'No access'), (self.owner, '  ')):
            with self.assertRaises(ValidationError):
                decide(self.finding, actor, 'unassign', reason)
        decide(self.finding, self.admin, 'unassign', 'Wrong assignment')
        self.assertIsNone(self.finding.owner_id)
        self.assertEqual(self.finding.workflow_state, 'unassigned')
        with self.assertRaises(ValidationError):
            decide(self.finding, self.admin, 'unassign', 'Already released')

    def test_release_keeps_evidence_invalidation_active(self):
        self.approve_owner()
        decide(self.finding, self.owner, 'unassign', 'Unavailable')
        invalidate(self.finding, 'Evidence changed')
        self.assertEqual(self.finding.approvals, {})
        self.assertEqual(self.finding.workflow_state, 'unassigned')

    def test_completed_release_requires_admin_reopen(self):
        self.approve_owner()
        decide(self.finding, self.reviewer, 'approve', 'Checked')
        decide(self.finding, self.owner, 'complete', 'Completed', ticket='CHG-1')
        with self.assertRaises(ValidationError):
            decide(self.finding, self.admin, 'unassign', 'Release')
        with self.assertRaises(ValidationError):
            decide(self.finding, self.owner, 'reopen', 'Reopen')
        decide(self.finding, self.admin, 'reopen', 'Completion recorded incorrectly')
        decide(self.finding, self.admin, 'unassign', 'Needs another owner')
        self.assertIsNone(self.finding.owner_id)
        self.assertEqual(self.finding.events.get(details__action='reopen').details['previous_change_ticket'], 'CHG-1')

    def test_single_release_rejects_stale_revision(self):
        self.assign()
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {'action': 'unassign', 'note': 'Unavailable', 'revision': self.finding.revision - 1})
        self.assertContains(response, 'changed while you were reviewing')
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.owner_id, self.owner.pk)
        response = self.client.post(self.url, {'action': 'unassign', 'note': 'Unavailable', 'revision': self.finding.revision})
        self.assertEqual(response.status_code, 302)
        self.finding.refresh_from_db()
        self.assertIsNone(self.finding.owner_id)

    def test_bulk_release_skips_stale_and_unauthorized_findings(self):
        self.assign()
        other = Finding.objects.create(environment=self.env, kind='unused', path='/other', name='Other',
            owner=self.reviewer, workflow_state='owner_review', first_seen=timezone.now(), last_seen=timezone.now(), evaluated_at=timezone.now())
        self.client.force_login(self.owner)
        url = reverse('findings', args=[self.env.pk])
        response = self.client.post(url, {'action': 'unassign', 'reason': 'Unavailable',
            'selected': [f'{self.finding.pk}:{self.finding.revision - 1}', f'{other.pk}:{other.revision}']}, follow=True)
        self.assertContains(response, 'skipped 2')
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.owner_id, self.owner.pk)
        response = self.client.post(url, {'action': 'unassign', 'reason': 'Unavailable',
            'selected': [f'{self.finding.pk}:{self.finding.revision}', f'{other.pk}:{other.revision}']}, follow=True)
        self.assertContains(response, 'Returned 1 finding(s)')
        self.assertContains(response, 'skipped 1')
        self.finding.refresh_from_db(); other.refresh_from_db()
        self.assertIsNone(self.finding.owner_id)
        self.assertEqual(other.owner_id, self.reviewer.pk)

    def test_bulk_release_requires_staff_reason_and_environment_match(self):
        self.assign()
        url = reverse('findings', args=[self.env.pk])
        payload = {'action': 'unassign', 'reason': 'Unavailable',
                   'selected': [f'{self.finding.pk}:{self.finding.revision}']}
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(url, payload).status_code, 403)
        self.client.force_login(self.owner)
        self.client.post(url, {**payload, 'reason': '  '})
        other = Environment.objects.create(name='Other', slug='other-env', manager='https://other.test')
        self.client.post(reverse('findings', args=[other.pk]), payload)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.owner_id, self.owner.pk)
        self.assertFalse(self.finding.events.filter(details__action='unassign').exists())
        response = self.client.get(url)
        self.assertContains(response, 'id="finding-selection-bar" hidden')
        self.assertContains(response, 'aria-labelledby="release-title"')
        self.assertContains(response, 'form="bulk-release" name="selected"')
        self.assertContains(response, 'finding-release.js')

    def test_bulk_assignment_assigns_unassigned_and_preserves_released_approvals(self):
        self.approve_owner()
        approvals = self.finding.approvals.copy()
        decide(self.finding, self.owner, 'unassign', 'Unavailable')
        other = Finding.objects.create(environment=self.env, kind='unused', path='/new', name='New',
            first_seen=timezone.now(), last_seen=timezone.now(), evaluated_at=timezone.now(), qualification='eligible')
        self.client.force_login(self.reviewer)
        url = reverse('findings', args=[self.env.pk]) + '?owner=none'
        page = self.client.get(url)
        self.assertContains(page, 'id="assign-open"')
        self.assertContains(page, 'id="finding-select-all"')
        response = self.client.post(url, {'action': 'assign', 'owner': self.reviewer.pk,
            'selected': [f'{self.finding.pk}:{self.finding.revision}', f'{other.pk}:{other.revision}']}, follow=True)
        self.assertContains(response, 'Assigned 2 finding(s)')
        self.finding.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(self.finding.owner_id, self.reviewer.pk)
        self.assertEqual(self.finding.approvals, approvals)
        self.assertEqual(self.finding.workflow_state, 'second_review')
        self.assertEqual(other.workflow_state, 'owner_review')
        self.assertTrue(other.events.filter(details__action='assign').exists())

    def test_bulk_assignment_rejects_invalid_owner_and_skips_stale_owned_or_foreign(self):
        self.client.force_login(self.owner)
        url = reverse('findings', args=[self.env.pk])
        payload = {'action': 'assign', 'owner': self.viewer.pk,
            'selected': [f'{self.finding.pk}:{self.finding.revision}']}
        self.client.post(url, payload)
        self.finding.refresh_from_db()
        self.assertIsNone(self.finding.owner_id)
        response = self.client.post(url, {**payload, 'owner': self.owner.pk,
            'selected': [f'{self.finding.pk}:{self.finding.revision + 1}']}, follow=True)
        self.assertContains(response, 'skipped 1')
        other_env = Environment.objects.create(name='Other', slug='assign-other', manager='https://other.test')
        self.client.post(reverse('findings', args=[other_env.pk]), {**payload, 'owner': self.owner.pk})
        self.finding.refresh_from_db()
        self.assertIsNone(self.finding.owner_id)
        self.assign()
        response = self.client.post(url, {**payload, 'owner': self.reviewer.pk,
            'selected': [f'{self.finding.pk}:{self.finding.revision}']}, follow=True)
        self.assertContains(response, 'already assigned; existing owner retained')
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.owner_id, self.owner.pk)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(url, payload).status_code, 403)

    def test_ready_release_and_reassignment_preserve_both_approvals(self):
        self.approve_owner()
        decide(self.finding, self.reviewer, 'approve', 'Independent check')
        approvals = self.finding.approvals.copy()
        decide(self.finding, self.owner, 'unassign', 'Unavailable')
        with self.assertRaises(ValidationError):
            decide(self.finding, self.admin, 'complete', 'Done', ticket='CHG-2')
        decide(self.finding, self.admin, 'assign', 'Take over completion', self.admin)
        self.assertEqual(self.finding.workflow_state, 'ready')
        self.assertEqual(self.finding.approvals, approvals)
        decide(self.finding, self.admin, 'complete', 'Done', ticket='CHG-2')

    def test_completed_page_offers_reopen_only_to_admin(self):
        self.finding.workflow_state = 'decommissioned'
        self.finding.save(update_fields=['workflow_state'])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url), 'Reopen completed review')
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(self.url), 'Reopen completed review')

    def approve_owner(self):
        self.assign()
        decide(self.finding, self.owner, 'approve', 'Checked membership and change impact.')

    def test_full_workflow_keeps_evidence_and_audit(self):
        self.approve_owner()
        decide(self.finding,self.reviewer,'approve','Independent check complete.')
        self.assertEqual(self.finding.workflow_state,'ready')
        with self.assertRaises(ValidationError):
            decide(self.finding,self.reviewer,'complete','Removed through change process.')
        decide(self.finding,self.reviewer,'complete','Removed through change process.',ticket='CHG-123')
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.workflow_state,'decommissioned')
        self.assertEqual(self.finding.change_ticket,'CHG-123')
        self.assertEqual(self.finding.approvals['owner']['evidence'],{'membership':'empty'})
        self.assertEqual(self.finding.events.count(),4)
        self.assertEqual(AuditEvent.objects.filter(action__startswith='finding.',target_id=str(self.finding.pk)).count(),4)
        snapshot_id=str(self.snapshot.pk)
        self.snapshot.delete()
        event=self.finding.events.get(details__action='approve',details__to='second_review')
        self.assertEqual(event.details['decision']['snapshot_id'],snapshot_id)
        self.owner.delete()
        event.refresh_from_db()
        self.assertEqual(event.actor_label,'owner · Local')

    def test_no_self_approval_even_for_administrator(self):
        self.assign(self.admin)
        decide(self.finding,self.admin,'approve','Checked.')
        with self.assertRaisesMessage(ValidationError,'different user'):
            decide(self.finding,self.admin,'approve','Second approval.')
        self.assertEqual(self.finding.workflow_state,'second_review')

    def test_permissions_owner_and_required_reason(self):
        with self.assertRaises(ValidationError): decide(self.finding,self.viewer,'assign','Assign',self.owner)
        self.assign()
        with self.assertRaises(ValidationError): decide(self.finding,self.reviewer,'approve','Checked')
        with self.assertRaises(ValidationError): decide(self.finding,self.owner,'approve','  ')
        with self.assertRaises(ValidationError): decide(self.finding,self.admin,'assign','Assign',self.viewer)

    def test_reassignment_and_rejection(self):
        self.approve_owner()
        decide(self.finding,self.admin,'assign','New owner',self.reviewer)
        self.assertEqual(self.finding.approvals,{})
        self.assertEqual(self.finding.workflow_state,'owner_review')
        decide(self.finding,self.reviewer,'reject','Required production group')
        decide(self.finding,self.admin,'reopen','New evidence available')
        self.assertEqual(self.finding.workflow_state,'owner_review')

    def test_stale_incomplete_and_changed_evidence_block_approval(self):
        self.approve_owner()
        self.snapshot.generated_at=timezone.now()-timedelta(days=365)
        self.snapshot.save()
        with self.assertRaisesMessage(ValidationError,'stale'): decide(self.finding,self.reviewer,'approve','Checked')
        self.snapshot.generated_at=timezone.now();self.snapshot.needs_review=True;self.snapshot.save()
        self.record.compact['membership']='unknown';self.record.save()
        with self.assertRaisesMessage(ValidationError,'membership'): decide(self.finding,self.reviewer,'approve','Checked')
        self.record.compact['membership']='empty';self.record.save()
        self.snapshot.needs_review=False;self.snapshot.save();SnapshotCoverage.objects.create(snapshot=self.snapshot)
        self.finding.evidence={'membership':'empty','unique_id':'replacement'}
        with self.assertRaisesMessage(ValidationError,'changed'): decide(self.finding,self.reviewer,'approve','Checked')

    def test_invalidation_keeps_prior_approval_in_history(self):
        self.approve_owner()
        invalidate(self.finding,'Configuration changed')
        self.finding.save(update_fields=FIELDS)
        self.assertEqual(self.finding.workflow_state,'owner_review')
        self.assertEqual(self.finding.approvals,{})
        self.assertEqual(self.finding.events.first().details['prior_approvals']['owner']['actor_name'],'owner · Local')

    def test_http_revision_permissions_and_queues(self):
        self.client.force_login(self.admin)
        data={'action':'assign','owner':self.owner.pk,'note':'Check manually','revision':0}
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertContains(self.client.post(self.url,data),'changed while you were reviewing')
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url,data).status_code,403)
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.finding.refresh_from_db()
        decide(self.finding,self.owner,'approve','Checked')
        self.client.force_login(self.reviewer)
        self.assertContains(self.client.get(reverse('findings',args=[self.env.pk]),{'review':'second_review'}),'Awaiting second approval')

    def test_notifications_and_history_export(self):
        self.assign()
        self.client.force_login(self.owner)
        self.assertTrue(any(item['label']=='Finding assigned to you' for item in self.client.get(reverse('notifications')).json()['items']))
        decide(self.finding,self.owner,'approve','Checked')
        self.assertFalse(any('independent' in item['label'] for item in self.client.get(reverse('notifications')).json()['items']))
        self.client.force_login(self.reviewer)
        self.assertTrue(any('independent' in item['label'] for item in self.client.get(reverse('notifications')).json()['items']))
        result=self.client.get(reverse('finding-history-export',args=[self.env.pk,self.finding.pk]))
        import json
        history=json.loads(b''.join(result.streaming_content))
        self.assertEqual(history[-1]['details']['decision']['actor_name'],'owner · Local')
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse('finding-history-export',args=[self.env.pk,self.finding.pk])).status_code,403)

    def test_collection_preserves_unchanged_and_invalidates_changed_approvals(self):
        from .models import FindingPolicy
        from .findings import synchronize
        FindingPolicy.objects.create(empty_group_days=0)
        self.approve_owner()
        def collect(**extra):
            Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),needs_review=True,report={'search_coverage':{'mode':'explicit_types'},'objects':[
                {'path':self.finding.path,'name':'A','membership':'empty',**extra}]})
            synchronize(self.env.pk)
            self.finding.refresh_from_db()
        collect()
        self.assertEqual(self.finding.workflow_state,'second_review')
        collect(unique_id='replacement-object')
        self.assertEqual(self.finding.workflow_state,'owner_review')
        self.assertEqual(self.finding.approvals,{})

    def test_keycloak_owner_names_and_provider_are_readable(self):
        from .models import KeycloakIdentity
        from .forms import FindingReviewForm
        self.owner.username='keycloak_internal_opaque_identifier'
        self.owner.first_name='Alex'; self.owner.last_name='Example'; self.owner.email='alex@example.com'; self.owner.save()
        KeycloakIdentity.objects.create(user=self.owner,issuer='https://sso.example/realms/test',subject='immutable-subject')
        form=FindingReviewForm(initial={'owner': self.owner.pk})
        choices=dict((str(key),label) for key,label in form.fields['owner'].choices)
        self.assertEqual(choices[str(self.owner.pk)],'Alex Example · alex@example.com · Keycloak')
        self.assertNotIn(str(self.viewer.pk),choices)
        self.assign()
        decide(self.finding,self.owner,'approve','Checked')
        self.assertEqual(self.finding.approvals['owner']['actor_id'],str(self.owner.pk))
        self.assertEqual(self.finding.approvals['owner']['actor_name'],'Alex Example · alex@example.com · Keycloak')
        self.client.force_login(self.admin)
        response=self.client.get(self.url)
        self.assertContains(response,'Alex Example')
        self.assertNotContains(response,'keycloak_internal_opaque_identifier')
        self.finding.approvals['owner']['actor_name']=self.owner.username
        self.finding.save(update_fields=['approvals'])
        self.assertNotContains(self.client.get(self.url),'keycloak_internal_opaque_identifier')

    def test_external_user_missing_profile_does_not_expose_internal_username(self):
        from .user_labels import user_label
        # Provider detection does not depend on the text of the internal username.
        from .models import KeycloakIdentity
        KeycloakIdentity.objects.create(user=self.owner,issuer='https://sso.example',subject='id')
        self.owner.username='keycloak_opaque';self.owner.save()
        label=user_label(self.owner)
        self.assertIn('account #'+str(self.owner.pk),label)
        self.assertNotIn('keycloak_opaque',label)

    def test_unrelated_coverage_and_missing_summary_do_not_block_empty_group(self):
        Snapshot.objects.filter(pk=self.snapshot.pk).update(needs_review=True)
        SnapshotCoverage.objects.filter(snapshot=self.snapshot).delete()
        self.approve_owner()
        decide(self.finding,self.reviewer,'approve','Independent review of confirmed membership')
        self.assertEqual(self.finding.workflow_state,'ready')

    def test_missing_or_excluded_object_blocks_approval(self):
        self.assign()
        self.record.compact['audit_exclusions']=['membership'];self.record.save()
        with self.assertRaisesMessage(ValidationError,'excluded'):
            decide(self.finding,self.owner,'approve','Checked')
        self.record.delete()
        with self.assertRaisesMessage(ValidationError,'not prepared'):
            decide(self.finding,self.owner,'approve','Checked')

    def test_kind_specific_coverage_guards(self):
        from .finding_workflow import relevant_evidence_error as check
        now=timezone.now()
        def error(kind,row,search=None): return check(kind,row,search,now,60)
        self.assertEqual(error('disabled',{'disabled':True}), '')
        self.assertTrue(error('disabled',{'disabled':False}))
        self.assertEqual(error('empty_policy',{'status':'empty','rule_count':0}), '')
        self.assertTrue(error('empty_policy',{'status':'empty','rule_count':None}))
        self.assertTrue(error('unused',{'usage':'unused_candidate'},{'mode':'explicit_types'}))
        self.assertTrue(error('unused',{'usage':'unused_candidate'}))
        self.assertEqual(error('unused',{'usage':'unused_candidate'},{'mode':'all_types'}),'')
        counters={'hit_status':'zero_hits','hit_count':0,'statistics_checked_at':now.isoformat()}
        self.assertEqual(error('zero_hits',counters),'')
        for patch in [{'hit_count':1},{'hit_status':'unknown'},{'statistics_checked_at':None},
                      {'statistics_checked_at':(now-timedelta(days=2)).isoformat()},
                      {'statistics_checked_at':(now+timedelta(minutes=1)).isoformat()}]:
            self.assertTrue(error('zero_hits',{**counters,**patch}))

    def test_approval_reads_only_compact_object(self):
        self.snapshot.summary = {'evidence_coverage': []}
        self.snapshot.save(update_fields=['summary'])
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from .finding_workflow import evidence_error
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(evidence_error(self.finding),'')
        self.assertFalse(any('"report"' in item['sql'] or '"data"' in item['sql'] for item in queries))

    def prepare_compatibility(self):
        self.finding.kind = 'unused'
        self.finding.evidence = {'usage': 'unused_candidate'}
        self.finding.save()
        self.record.compact = {'path': self.finding.path, 'usage': 'unused_candidate'}
        self.record.save()
        Snapshot.objects.filter(pk=self.snapshot.pk).update(report={'search_coverage': {
            'mode': 'explicit_types', 'resource_types': ['Group', 'Service']}})

    def test_manual_route_requires_independent_attestations_and_audits_them(self):
        self.prepare_compatibility()
        self.assign()
        checks = dict(manual_verified=True, manual_checks='Checked external policies and service dependencies.', evidence_reference='CHG-42')
        for missing in checks:
            invalid = {**checks, missing: False if missing == 'manual_verified' else ''}
            with self.assertRaises(ValidationError):
                decide(self.finding, self.owner, 'approve', 'Owner checks', **invalid)
        decide(self.finding, self.owner, 'approve', 'Owner checks', **checks)
        with self.assertRaises(ValidationError):
            decide(self.finding, self.owner, 'approve', 'Cannot approve twice', **checks)
        with self.assertRaises(ValidationError):
            decide(self.finding, self.reviewer, 'approve', 'Needs own checks')
        decide(self.finding, self.reviewer, 'approve', 'Independent checks', **{**checks, 'evidence_reference': 'CHG-43'})
        self.assertEqual(self.finding.workflow_state, 'ready')
        decision = self.finding.events.filter(details__action='approve', details__to='ready').get().details['decision']
        self.assertTrue(decision['manual_verified'])
        self.assertEqual(decision['evidence_reference'], 'CHG-43')
        self.assertEqual(decision['coverage']['mode'], 'explicit_types')
        self.snapshot.refresh_from_db()
        self.assertEqual(self.snapshot.report['search_coverage']['mode'], 'explicit_types')
        decide(self.finding, self.reviewer, 'complete', 'Change completed', ticket='CHG-43')
        self.assertEqual(self.finding.workflow_state, 'decommissioned')

    def test_manual_route_never_overrides_failed_missing_or_contradictory_evidence(self):
        from .finding_workflow import approval_readiness
        self.prepare_compatibility()
        for coverage in ({}, {'mode':'explicit_types'}, {'mode':'explicit_types','resource_types':['Group'],'errors':['timeout']}, {'mode':'all_types','failed':True}):
            Snapshot.objects.filter(pk=self.snapshot.pk).update(report={'search_coverage':coverage})
            self.assertEqual(approval_readiness(self.finding)['status'], 'blocked')
        self.prepare_compatibility()
        self.record.compact['usage'] = 'used'
        self.record.save()
        self.assertEqual(approval_readiness(self.finding)['status'], 'blocked')

    def test_scope_change_requires_new_owner_review(self):
        from .finding_workflow import coverage_changed, coverage_key
        self.prepare_compatibility()
        self.assign()
        checks = dict(manual_verified=True, manual_checks='Independent dependency check', evidence_reference='CHG-42')
        decide(self.finding, self.owner, 'approve', 'Checked', **checks)
        self.assertFalse(coverage_changed(self.finding, {'mode':'explicit_types','resource_types':['Service','Group','Group']}))
        new_scope = {'mode':'explicit_types','resource_types':['Group']}
        self.assertTrue(coverage_changed(self.finding, new_scope))
        Snapshot.objects.filter(pk=self.snapshot.pk).update(report={'search_coverage':new_scope})
        with self.assertRaisesMessage(ValidationError, 'coverage changed'):
            decide(self.finding, self.reviewer, 'approve', 'Checked', **checks)
        coverage_key({'resource_types': [None, 1, 'Group']})

    def test_preflight_and_failed_form_preserve_manual_notes(self):
        self.prepare_compatibility()
        self.assign()
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertContains(response, 'Limited dependency-search coverage — additional checks required')
        self.assertContains(response, '<strong>Searched object types:</strong> Group, Service', html=False)
        self.assertContains(response, 'separate from the number of approvals required')
        self.assertContains(response, 'Approve review')
        self.assertNotContains(response, 'name="action" id="id_action"')
        response = self.client.post(self.url, {'revision':self.finding.revision, 'action':'approve', 'note':'Keep my reasoning', 'manual_checks':'Keep my dependency checks'})
        self.assertContains(response, 'Keep my reasoning')
        self.assertContains(response, 'Keep my dependency checks')
        self.assertContains(response, 'Confirm your independent dependency check')

    def test_single_approval_and_completion(self):
        from .models import WorkspacePolicy
        WorkspacePolicy.objects.create(required_approvals=1)
        self.assign()
        with self.assertRaises(ValidationError):
            decide(self.finding, self.reviewer, 'approve', 'Not owner')
        decide(self.finding, self.owner, 'approve', 'Verified dependencies')
        self.assertEqual(self.finding.workflow_state, 'ready')
        self.assertEqual(self.finding.events.latest('pk').details['required_approvals'], 1)
        decide(self.finding, self.owner, 'complete', 'Removed manually', ticket='CHG-1')
        self.assertEqual(self.finding.workflow_state, 'decommissioned')

    def test_setting_changes_only_on_new_assignment_or_reopen(self):
        from .models import WorkspacePolicy
        self.assign()
        policy = WorkspacePolicy.objects.create(required_approvals=1)
        decide(self.finding, self.owner, 'approve', 'Checked')
        self.assertEqual(self.finding.workflow_state, 'second_review')
        decide(self.finding, self.reviewer, 'reject', 'Needs another review')
        decide(self.finding, self.admin, 'reopen', 'Restart with current policy')
        self.assertEqual(self.finding.required_approvals, 1)
        policy.required_approvals = 2
        policy.save()
        decide(self.finding, self.owner, 'approve', 'Checked again')
        self.assertEqual(self.finding.workflow_state, 'ready')
        decide(self.finding, self.admin, 'assign', 'Change owner', self.reviewer)
        self.assertEqual(self.finding.required_approvals, 2)
        self.assertEqual(self.finding.approvals, {})

    def test_approval_setting_is_admin_only_and_validated(self):
        from .models import WorkspacePolicy
        url = reverse('review-approvals')
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(url, {'required_approvals': 1}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {'required_approvals': 3}).status_code, 200)
        self.assertFalse(WorkspacePolicy.objects.exists())
        self.assertEqual(self.client.post(url, {'required_approvals': 1}).status_code, 302)
        self.assertEqual(WorkspacePolicy.objects.get(pk=1).required_approvals, 1)
        self.assign()
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertContains(response, 'Single approval')
        self.assertNotContains(response, 'Independent review</')

    def test_unassigned_page_previews_current_approval_policy_without_writes(self):
        from .models import WorkspacePolicy
        WorkspacePolicy.objects.create(required_approvals=1)
        self.client.force_login(self.owner)
        revision = self.finding.revision
        response = self.client.get(self.url)
        self.assertContains(response, 'Single approval')
        self.assertNotContains(response, 'Two-person approval')
        self.assertNotContains(response, 'Independent review</')
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.required_approvals, 2)
        self.assertEqual(self.finding.revision, revision)
        self.assertFalse(self.finding.events.exists())
        self.assign()
        self.assertEqual(self.finding.required_approvals, 1)

    def test_assigned_page_keeps_captured_approval_policy(self):
        from .models import WorkspacePolicy
        self.assign()
        WorkspacePolicy.objects.update_or_create(pk=1, defaults={'required_approvals': 1})
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertContains(response, 'Two-person approval')
        self.assertContains(response, 'Independent review</')

    def test_setting_restarts_existing_review_and_archives_approval(self):
        self.assign()
        decide(self.finding, self.owner, 'approve', 'Original owner checks')
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.workflow_state, 'second_review')
        prior = self.finding.approvals.copy()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('review-approvals'), {'required_approvals': 1}).status_code, 302)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.required_approvals, 1)
        self.assertEqual(self.finding.workflow_state, 'owner_review')
        self.assertEqual(self.finding.approvals, {})
        event = self.finding.events.latest('pk')
        self.assertEqual(event.details['prior_approvals'], prior)
        self.assertEqual(event.details['previous_required_approvals'], 2)
        self.assertContains(self.client.get(self.url), 'Single approval')
        decide(self.finding, self.owner, 'approve', 'Fresh owner checks')
        self.assertEqual(self.finding.workflow_state, 'ready')

    def test_setting_does_not_rewrite_completed_reviews(self):
        self.assign()
        decide(self.finding, self.owner, 'approve', 'Owner checks')
        decide(self.finding, self.reviewer, 'approve', 'Second checks')
        decide(self.finding, self.owner, 'complete', 'Removed', ticket='CHG-2')
        self.client.force_login(self.admin)
        self.client.post(reverse('review-approvals'), {'required_approvals': 1})
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.required_approvals, 2)
        self.assertEqual(self.finding.workflow_state, 'decommissioned')
        self.assertEqual(len(self.finding.approvals), 2)

    def test_single_approval_preserves_evidence_guard(self):
        from .models import WorkspacePolicy
        WorkspacePolicy.objects.create(required_approvals=1)
        self.assign()
        self.record.delete()
        with self.assertRaises(ValidationError):
            decide(self.finding, self.owner, 'approve', 'Checked')
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.workflow_state, 'owner_review')

    def test_recalculation_blocks_approvals_and_completion_for_affected_kinds(self):
        from .models import FindingRecalculation
        from .finding_workflow import approval_readiness
        self.assign()
        job = FindingRecalculation.objects.create(environment=self.env, kinds=['empty_group'])
        for status in ('queued', 'running', 'failed'):
            job.status = status; job.save()
            with self.subTest(status=status):
                self.assertEqual(approval_readiness(self.finding)['status'], 'blocked')
                with self.assertRaisesMessage(ValidationError, 'recalculation'):
                    decide(self.finding, self.owner, 'approve', 'Checked')
                self.finding.refresh_from_db()
                self.assertEqual(self.finding.workflow_state, 'owner_review')
        job.status = 'completed'; job.save()
        decide(self.finding, self.owner, 'approve', 'Checked')
        job.status = 'queued'; job.save()
        with self.assertRaisesMessage(ValidationError, 'recalculation'):
            decide(self.finding, self.reviewer, 'approve', 'Checked')
        job.status = 'completed'; job.save()
        decide(self.finding, self.reviewer, 'approve', 'Checked')
        job.status = 'failed'; job.save()
        with self.assertRaisesMessage(ValidationError, 'recalculation'):
            decide(self.finding, self.owner, 'complete', 'Completed', ticket='CHG-test')
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.workflow_state, 'ready')
        job.status = 'completed'; job.save()
        decide(self.finding, self.owner, 'complete', 'Completed', ticket='CHG-test')
        self.assertEqual(self.finding.workflow_state, 'decommissioned')

    def test_unrelated_recalculation_does_not_block_and_rejection_remains_available(self):
        from .models import FindingRecalculation
        from .finding_workflow import approval_readiness
        self.assign()
        job = FindingRecalculation.objects.create(environment=self.env, kinds=['zero_hits'], status='running')
        self.assertEqual(approval_readiness(self.finding)['status'], 'ready')
        decide(self.finding, self.owner, 'approve', 'Checked')
        job.kinds = ['empty_group']; job.save()
        decide(self.finding, self.reviewer, 'reject', 'Do not remove')
        self.assertEqual(self.finding.workflow_state, 'rejected')

    def test_released_reviews_leave_actionable_queues_and_resume_on_assignment(self):
        self.approve_owner()
        self.client.force_login(self.reviewer)
        for stage, queue, label in (
            ('second_review', 'second', 'Awaiting second approval'),
            ('ready', 'ready', 'Ready for decommissioning'),
        ):
            with self.subTest(stage=stage):
                approvals = self.finding.approvals.copy()
                decide(self.finding, self.owner, 'unassign', 'Unavailable')
                self.finding.refresh_from_db()
                self.assertEqual(self.finding.workflow_state, stage)
                self.assertEqual(self.finding.approvals, approvals)
                review_url = reverse('findings', args=[self.env.pk])
                self.assertEqual(self.client.get(review_url, {'review': stage}).context['page'].paginator.count, 0)
                self.assertEqual(self.client.get(reverse('my-work'), {'queue': queue}).context['page'].paginator.count, 0)
                response = self.client.get(review_url, {'owner': 'none'})
                self.assertContains(response, label)
                self.assertEqual(response.context['page'].paginator.count, 1)
                self.assertContains(self.client.get(self.url), label)
                self.assertFalse(any('independent' in item['label'] for item in self.client.get(reverse('notifications')).json()['items']))
                decide(self.finding, self.admin, 'assign', 'Resume work', self.owner)
                self.assertEqual(self.finding.approvals, approvals)
                self.assertEqual(self.client.get(review_url, {'review': stage}).context['page'].paginator.count, 1)
                self.assertEqual(self.client.get(reverse('my-work'), {'queue': queue}).context['page'].paginator.count, 1)
                self.assertEqual(self.client.get(review_url, {'owner': 'none'}).context['page'].paginator.count, 0)
                if stage == 'second_review':
                    decide(self.finding, self.reviewer, 'approve', 'Independent checks complete')

    def test_assign_owner_defaults_to_current_reviewer_without_assigning(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(self.url)
        form = response.context['form']
        self.assertEqual(form['owner'].value(), self.reviewer.pk)
        self.assertContains(response, f'value="{self.reviewer.pk}" selected')
        self.finding.refresh_from_db()
        self.assertIsNone(self.finding.owner_id)
        self.assertFalse(self.finding.events.exists())
        # Preserve another selection and an explicitly cleared field on errors.
        for selected in (str(self.owner.pk), ''):
            response = self.client.post(self.url, {'action': 'assign', 'owner': selected, 'revision': -1})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['form']['owner'].value(), selected)
        self.assign()
        response = self.client.get(self.url)
        self.assertEqual(response.context['form']['owner'].value(), self.owner.pk)

    def test_ownerless_assignment_needs_no_note_but_reassignment_does(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {'action': 'assign', 'owner': self.owner.pk, 'revision': self.finding.revision})
        self.assertEqual(response.status_code, 302)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.owner_id, self.owner.pk)
        self.assertEqual(self.finding.events.first().details['reason'], 'Owner assigned.')
        response = self.client.post(self.url, {'action': 'assign', 'owner': self.reviewer.pk, 'revision': self.finding.revision})
        self.assertEqual(response.status_code, 200)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.owner_id, self.owner.pk)
        with self.assertRaises(ValidationError):
            decide(self.finding, self.admin, 'assign', '', self.reviewer)
        with self.assertRaises(ValidationError):
            decide(self.finding, self.owner, 'approve', '')
        decide(self.finding, self.owner, 'unassign', 'Release')
        decide(self.finding, self.admin, 'assign', '', self.reviewer)
        self.assertEqual(self.finding.owner_id, self.reviewer.pk)

    def test_review_return_links_are_local_and_preserved_after_assignment(self):
        from urllib.parse import urlencode
        self.client.force_login(self.admin)
        return_to = reverse('findings', args=[self.env.pk]) + '?owner=none&q=A&page=2'
        url = self.url + '?' + urlencode({'return_to': return_to})
        response = self.client.get(url)
        self.assertEqual(response.context['review_return'], return_to)
        response = self.client.post(url, {'action':'assign', 'owner':self.owner.pk, 'revision':self.finding.revision})
        self.assertEqual(response.url, url)
        for invalid in ('https://evil.example/', '//evil.example/', '/administration/', '/environments/999/findings/', 'http://['):
            response = self.client.get(self.url, {'return_to':invalid})
            self.assertEqual(response.context['review_return'], reverse('findings', args=[self.env.pk]))

    def test_review_neighbours_follow_filter_and_sort_across_pages(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from urllib.parse import urlsplit, parse_qs
        self.client.force_login(self.admin)
        other = Finding.objects.create(environment=self.env, snapshot=self.snapshot, kind='empty_group',
            path='/infra/groups/b', name='B', qualification='eligible', fingerprint='b',
            first_seen=timezone.now(), last_seen=timezone.now(), evaluated_at=timezone.now())
        return_to = reverse('findings', args=[self.env.pk]) + '?owner=none&sort=name&direction=desc&page=2'
        with patch('inventory.review_navigation.preferences', return_value=SimpleNamespace(page_size=1)):
            response = self.client.get(self.url, {'return_to':return_to})
        previous = response.context['review_navigation']['previous']
        self.assertEqual(urlsplit(previous).path, reverse('finding-detail', args=[self.env.pk,other.pk]))
        self.assertIn('page=1', parse_qs(urlsplit(previous).query)['return_to'][0])
        self.assertNotIn('next', response.context['review_navigation'])
        self.assign()
        response = self.client.get(self.url, {'return_to':return_to})
        self.assertTrue(response.context['review_navigation']['changed'])

    def test_active_queue_and_decision_blocker_are_visible(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('findings', args=[self.env.pk]), {'owner':'none'})
        self.assertContains(response, 'href="?owner=none" aria-current="page"')
        self.assign()
        Finding.objects.filter(pk=self.finding.pk).update(qualification='insufficient')
        response = self.client.get(self.url)
        self.assertContains(response, 'id="decision-blocker"')
        self.assertContains(response, 'aria-describedby="decision-blocker"')
        self.assertContains(response, 'Review coverage')

    def test_first_approver_is_not_offered_second_approval_after_reassignment(self):
        self.approve_owner()
        decide(self.finding, self.owner, 'unassign', 'Unavailable')
        decide(self.finding, self.admin, 'assign', '', self.reviewer)
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertFalse(response.context['can_decide'])
        self.assertContains(response, 'The current owner and the first approver cannot provide the second approval.')

    def recovery_fixture(self):
        from .models import SnapshotFindingAssessment, SnapshotFindingEvidence, FindingRecalculation
        from .observation import condition_fingerprint
        self.finding.evidence = {'membership': 'empty', 'unique_id': 'stable-object'}
        self.finding.save()
        self.approve_owner()
        original = self.finding.approvals['owner'].copy()
        stamp = self.snapshot.generated_at + timedelta(microseconds=1)
        current = Snapshot.objects.create(environment=self.env, generated_at=stamp, report={})
        SnapshotRecord.objects.create(snapshot=current, ordinal=1, view='inventory', name='A', sort_name='a',
            compact={'path': self.finding.path, 'membership': 'empty'}, data={}, columns=[], sort_values={}, search_basic='', search_evidence='')
        SnapshotFindingAssessment.objects.create(snapshot=current, path=self.finding.path, kind=self.finding.kind,
            assessment={'status':'observing', 'reason':'Observation restarted after a gap exceeding 24.0 hours.'})
        invalidate(self.finding, 'Current evidence no longer qualifies under the finding criteria.')
        self.finding.snapshot = current
        self.finding.observation_started = self.snapshot.generated_at
        self.finding.save()
        for snapshot in (self.snapshot, current):
            SnapshotFindingEvidence.objects.create(snapshot=snapshot, path=self.finding.path, kind=self.finding.kind,
                name='A', fingerprint=condition_fingerprint(self.finding.kind, self.finding.evidence))
        FindingRecalculation.objects.create(environment=self.env, kinds=[self.finding.kind], status='completed', finished_at=timezone.now())
        return original, current

    def test_gap_recovery_preview_confirmation_and_independent_review(self):
        original, current = self.recovery_fixture()
        url = reverse('approval-recovery', args=[self.env.pk, self.finding.pk])
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'confirm':'yes'}).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(url)
        result = response.context['recovery']
        self.assertTrue(result['allowed'], result)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.approvals, {})
        data = {'revision':self.finding.revision, 'event_id':result['event_id']}
        self.assertEqual(self.client.post(url, data).status_code, 200)
        data['confirm'] = 'yes'
        self.assertEqual(self.client.post(url, dict(data, revision=-1)).status_code, 200)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.approvals, {'owner':original})
        self.assertEqual(self.finding.workflow_state, 'second_review')
        self.assertEqual(self.finding.events.first().details['action'], 'approval_recovered')
        with self.assertRaises(ValidationError):
            decide(self.finding, self.owner, 'approve', 'Cannot approve twice')
        self.assertEqual(self.client.post(url, data).status_code, 200)

    def test_gap_recovery_blocks_missing_proof_and_changed_evidence(self):
        from .approval_recovery import preview
        from .models import SnapshotFindingAssessment
        original, current = self.recovery_fixture()
        self.finding.evidence['unique_id'] = 'replacement'
        self.assertFalse(preview(self.finding)['allowed'])
        self.finding.evidence = original['evidence']
        current.needs_review = True; current.save()
        self.assertFalse(preview(self.finding)['allowed'])
        current.needs_review = False; current.save()
        SnapshotFindingAssessment.objects.filter(snapshot=current).delete()
        self.assertFalse(preview(self.finding)['allowed'])

    def test_gap_recovery_rechecks_workflow_and_recalculation(self):
        from .approval_recovery import preview
        from .models import FindingRecalculation
        original, current = self.recovery_fixture()
        invalidation = self.finding.events.filter(details__action='approval_invalidated').first()
        details = invalidation.details.copy()
        invalidation.details = dict(details, reason='Relevant evidence changed or the condition was observed again.')
        invalidation.save()
        self.assertFalse(preview(self.finding)['allowed'])
        invalidation.details = details; invalidation.save()
        self.finding.owner = self.reviewer
        self.assertFalse(preview(self.finding)['allowed'])
        self.finding.owner = self.owner
        job = FindingRecalculation.objects.get(environment=self.env)
        job.status = 'queued'; job.save()
        self.assertFalse(preview(self.finding)['allowed'])
        job.status = 'completed'; job.save()
        self.assertTrue(preview(self.finding)['allowed'])
        self.finding.events.create(message='Owner changed', details={'action':'assign'})
        self.assertFalse(preview(self.finding)['allowed'])
