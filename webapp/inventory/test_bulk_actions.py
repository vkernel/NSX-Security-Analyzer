from unittest.mock import patch
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Finding, DeletionBatch, DeletionItem, AuditJob, Snapshot, SnapshotRecord

@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class BulkActionTests(TestCase):
    def setUp(self):
        users=get_user_model().objects
        self.admin=users.create_superuser('bulk-admin', password='test-only')
        self.operator=users.create_user('bulk-operator', is_staff=True)
        self.viewer=users.create_user('bulk-viewer')
        self.env=Environment.objects.create(name='Bulk',slug='bulk',manager='https://nsx.example')
        self.finding=Finding.objects.create(environment=self.env,name='Group',path='/infra/domains/default/groups/g',kind='empty_group',qualification='eligible',first_seen=timezone.now(),last_seen=timezone.now(),evaluated_at=timezone.now())
        self.client.force_login(self.admin)

    def post(self, kind, action, selected, **extra):
        return self.client.post(reverse('bulk-actions', args=[kind]), {'action':action,'selected':selected,'return_to':'/my-work/?queue=unassigned',**extra})

    def test_assignment_requires_confirmation_and_skips_stale(self):
        token=f'{self.finding.pk}:{self.finding.revision}'
        self.assertContains(self.post('findings','assign_me',[token]),'I confirm this action')
        self.finding.refresh_from_db();self.assertIsNone(self.finding.owner_id)
        self.assertContains(self.post('findings','assign_me',[token],confirm='yes'),'1 succeeded')
        self.finding.refresh_from_db();self.assertEqual(self.finding.owner_id,self.admin.pk)
        self.assertContains(self.post('findings','release',[token],confirm='yes',reason='Not available'),'Changed since selection')
        self.finding.refresh_from_db();self.assertEqual(self.finding.owner_id,self.admin.pk)

    def test_reassignment_permissions_reason_and_approval_reset(self):
        from .finding_workflow import decide
        decide(self.finding,self.admin,'assign','',owner=self.admin)
        self.finding.approvals={'owner':{'actor_id':str(self.admin.pk)}}
        self.finding.workflow_state='second_review';self.finding.save()
        token=f'{self.finding.pk}:{self.finding.revision}'
        self.client.force_login(self.operator)
        self.assertContains(self.post('findings','reassign',[token],confirm='yes',owner=self.operator.pk,reason='Take over'),'Only the owner or an administrator')
        self.client.force_login(self.admin)
        self.assertContains(self.post('findings','reassign',[token],confirm='yes',owner=self.operator.pk),'Provide a reason')
        self.assertContains(self.post('findings','reassign',[token],confirm='yes',owner=self.operator.pk,reason='Take over'),'1 succeeded')
        self.finding.refresh_from_db();self.assertEqual(self.finding.approvals,{})
        self.assertEqual(self.finding.owner_id,self.operator.pk)

    def test_cancel_collections_never_stops_running_job(self):
        queued=AuditJob.objects.create(environment=self.env,status='queued')
        other=Environment.objects.create(name='Other',slug='other-bulk',manager='https://other.example')
        running=AuditJob.objects.create(environment=other,status='running')
        with patch('inventory.services.request_collection_stop') as stop:
            response=self.post('collections','cancel',[str(queued.pk),str(running.pk)],confirm='yes',reason='No longer required')
            stop.assert_called_once_with(queued.pk,self.admin)
            self.assertContains(response,'1 succeeded · 1 skipped')
            self.assertContains(response,'Running collections must be stopped individually')

    def test_queued_collection_is_cancelled_and_audited(self):
        from .models import AuditEvent
        queued=AuditJob.objects.create(environment=self.env,status='queued')
        response=self.post('collections','cancel',[str(queued.pk)],confirm='yes',reason='Withdraw queued run')
        self.assertContains(response,'1 succeeded')
        queued.refresh_from_db();self.assertEqual(queued.status,'cancelled')
        self.assertTrue(AuditEvent.objects.filter(action='collection.bulk_cancelled',target_id=str(queued.pk)).exists())

    def test_cleanup_cancellation_does_not_cancel_started_work(self):
        batch=DeletionBatch.objects.create(environment=self.env,requested_by=self.admin,manager=self.env.manager,status='preview_ready')
        pending=DeletionItem.objects.create(batch=batch,name='Pending',path='/a',revision=0,status='cleanup_review',cleanup_plan=[{'name':'Rule'}])
        started=DeletionItem.objects.create(batch=batch,name='Started',path='/b',revision=0,status='sending',cleanup_plan=[{'name':'Rule'}])
        self.assertContains(self.post('cleanup','cancel',[str(pending.pk),str(started.pk)],confirm='yes',reason='Withdraw'),'1 succeeded · 1 skipped')
        pending.refresh_from_db();started.refresh_from_db()
        self.assertEqual(pending.status,'cancelled');self.assertEqual(started.status,'sending')

    def test_history_export_retains_objects_and_missing_configuration(self):
        batch=DeletionBatch.objects.create(environment=self.env,requested_by=self.admin,manager=self.env.manager,status='completed')
        DeletionItem.objects.create(batch=batch,name='Deleted',path='/a',revision=0,status='deleted',before={'id':'a'})
        DeletionItem.objects.create(batch=batch,name='Blocked',path='/b',revision=0,status='blocked')
        response=self.post('history','configurations',[str(batch.pk)])
        self.assertEqual(response.status_code,200)
        import json
        items=json.loads(response.content)[0]['objects']
        self.assertEqual(items[0]['configuration'],{'id':'a'})
        self.assertFalse(items[1]['configuration_available'])
        self.client.force_login(self.operator)
        self.assertEqual(self.post('history','export',[str(batch.pk)]).status_code,403)

    def test_inventory_export_and_comparison_are_snapshot_scoped(self):
        snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={})
        SnapshotRecord.objects.create(snapshot=snapshot,ordinal=7,view='inventory',name='Group',sort_name='group',compact={'name':'Group','kind':'group','path':self.finding.path},data={'name':'Group','kind':'group'},columns=[],sort_values={},search_basic='',search_evidence='')
        self.client.force_login(self.viewer)
        response=self.post('inventory','compare',['7'],snapshot=str(snapshot.pk))
        self.assertContains(response,'Saved object details');self.assertContains(response,reverse('finding-detail',args=[self.env.pk,self.finding.pk]))
        self.assertContains(self.post('inventory','export',['7'],snapshot=str(snapshot.pk)),'Group')
        self.assertEqual(self.post('findings','export',[str(self.finding.pk)]).status_code,403)

    def test_limits_bad_selection_and_safe_return(self):
        self.assertEqual(self.post('findings','assign_me',[]).status_code,400)
        self.assertEqual(self.post('findings','assign_me',[str(i) for i in range(101)]).status_code,400)
        self.assertEqual(self.post('findings','assign_me',['not-an-id']).status_code,400)
        response=self.post('findings','assign_me',[str(self.finding.pk)],return_to='//evil.test')
        self.assertNotContains(response,'//evil.test')

    def test_unassigned_my_work_and_page_controls_render(self):
        self.assertContains(self.client.get(reverse('my-work'),{'queue':'unassigned'}),'assign_me')
        self.assertContains(self.client.get(reverse('all-collections')),'bulk-collections')
        self.assertContains(self.client.get(reverse('deletion-history',args=[self.env.pk])),'bulk-history')
