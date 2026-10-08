from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Finding, Snapshot, SnapshotCoverage, AuditEvent
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
        self.assertEqual(event.actor_label,'owner')

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
        with self.assertRaisesMessage(ValidationError,'coverage'): decide(self.finding,self.reviewer,'approve','Checked')
        self.snapshot.needs_review=False;self.snapshot.save();SnapshotCoverage.objects.create(snapshot=self.snapshot)
        self.finding.evidence={'membership':'empty','unique_id':'replacement'}
        with self.assertRaisesMessage(ValidationError,'changed'): decide(self.finding,self.reviewer,'approve','Checked')

    def test_invalidation_keeps_prior_approval_in_history(self):
        self.approve_owner()
        invalidate(self.finding,'Configuration changed')
        self.finding.save(update_fields=FIELDS)
        self.assertEqual(self.finding.workflow_state,'owner_review')
        self.assertEqual(self.finding.approvals,{})
        self.assertEqual(self.finding.events.first().details['prior_approvals']['owner']['actor_name'],'owner')

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
        self.assertEqual(history[-1]['details']['decision']['actor_name'],'owner')
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse('finding-history-export',args=[self.env.pk,self.finding.pk])).status_code,403)

    def test_collection_preserves_unchanged_and_invalidates_changed_approvals(self):
        from .models import FindingPolicy
        from .findings import synchronize
        FindingPolicy.objects.create(empty_group_days=0)
        self.approve_owner()
        def collect(**extra):
            Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={'objects':[
                {'path':self.finding.path,'name':'A','membership':'empty',**extra}]})
            synchronize(self.env.pk)
            self.finding.refresh_from_db()
        collect()
        self.assertEqual(self.finding.workflow_state,'second_review')
        collect(unique_id='replacement-object')
        self.assertEqual(self.finding.workflow_state,'owner_review')
        self.assertEqual(self.finding.approvals,{})
