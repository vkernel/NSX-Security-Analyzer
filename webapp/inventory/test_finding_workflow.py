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

    def assign(self, owner=None):
        decide(self.finding, self.admin, 'assign', 'Please check dependencies.', owner or self.owner)

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
        self.assertContains(response, 'Manual verification required')
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

    def test_single_approval_preserves_evidence_guard(self):
        from .models import WorkspacePolicy
        WorkspacePolicy.objects.create(required_approvals=1)
        self.assign()
        self.record.delete()
        with self.assertRaises(ValidationError):
            decide(self.finding, self.owner, 'approve', 'Checked')
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.workflow_state, 'owner_review')
