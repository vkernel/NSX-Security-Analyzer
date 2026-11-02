from datetime import timedelta
from io import StringIO
import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import deletion as d, deletion_plan as plan
from .models import WorkspacePolicy, DeletionBatch, DeletionItem
from . import test_reference_cleanup as cleanup_tests

@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ConsolidatedPlanTests(TestCase):
    setUp=cleanup_tests.ReferenceCleanupTests.setUp
    live=cleanup_tests.ReferenceCleanupTests.live
    preview=cleanup_tests.ReferenceCleanupTests.preview
    approve=cleanup_tests.ReferenceCleanupTests.approve
    def url(self): return reverse('deletion-plan',args=[self.batch.pk])
    def token(self):
        self.batch.refresh_from_db()
        return plan.fingerprint(self.batch,list(self.batch.items.order_by('pk')))
    def authorize(self, **extra):
        with patch.object(d,'approvals_valid',return_value={}):
            return self.client.post(self.url(),dict(action='authorize',fingerprint=self.token(),ticket='CHG-automatic',execute_after_approval='yes',**extra))
    def test_complete_plan_approval_then_authorized_execution_is_queued_not_sent(self):
        self.preview()
        WorkspacePolicy.objects.update_or_create(pk=1,defaults={'required_approvals':2})
        self.assertContains(self.client.get(self.url()),'Review the complete deletion plan')
        self.assertEqual(self.authorize().status_code,302)
        self.batch.refresh_from_db();self.assertEqual(self.batch.execution_intent['state'],'armed')
        self.approve(revalidate=False)
        self.batch.refresh_from_db();self.assertEqual(self.batch.status,'preview_ready')
        second=get_user_model().objects.create_superuser('plan-second',password='test')
        self.client.force_login(second)
        with patch.object(d,'approvals_valid',return_value={}):
            response=self.client.post(self.url(),{'action':'approve','fingerprint':self.token(),'approve':'yes'})
        self.assertEqual(response.status_code,302)
        self.batch.refresh_from_db();self.item.refresh_from_db()
        self.assertEqual(self.batch.status,'queued')
        self.assertEqual(self.batch.confirmed_by,self.user)
        self.assertEqual(len(self.item.cleanup_approvals),2)
        self.assertEqual(self.batch.ticket,'CHG-automatic')
        self.nsx.http.request.assert_not_called()
    def test_status_explains_next_reviewer_and_does_not_offer_duplicate_approval(self):
        self.preview()
        WorkspacePolicy.objects.update_or_create(pk=1, defaults={'required_approvals':2})
        self.approve(revalidate=False)
        response = self.client.get(self.url())
        self.assertContains(response, 'Your approval is recorded')
        self.assertNotContains(response, 'Record my approval for this plan')
        second = get_user_model().objects.create_superuser('next-plan-reviewer', password='test')
        self.client.force_login(second)
        response = self.client.get(self.url())
        self.assertContains(response, 'Record my approval for this plan')
        self.assertContains(response, 'deletion-plan-status')
        self.nsx.http.request.assert_not_called()

    def test_non_review_status_and_stale_verification_are_readable(self):
        self.preview(); self.approve()
        self.item.verified_at = timezone.now()-timedelta(minutes=11); self.item.save()
        self.assertContains(self.client.get(self.url()), 'Verification needs refreshing')
        self.batch.status='checking'; self.batch.save()
        self.assertContains(self.client.get(self.url()), 'Checking live deletion preview')

    def test_opt_in_requires_ticket_and_exact_plan(self):
        self.preview()
        for data in [{'action':'authorize','fingerprint':self.token(),'ticket':'CHG'},
                     {'action':'authorize','fingerprint':'stale','ticket':'CHG','execute_after_approval':'yes'}]:
            with patch.object(d,'approvals_valid',return_value={}):
                self.assertEqual(self.client.post(self.url(),data).status_code,200)
            self.batch.refresh_from_db();self.assertEqual(self.batch.execution_intent,{})
    def test_expiry_and_revocation_do_not_queue_execution(self):
        self.preview();self.authorize()
        self.batch.refresh_from_db();self.batch.execution_intent['expires_at']=(timezone.now()-timedelta(seconds=1)).isoformat();self.batch.save()
        self.approve()
        self.batch.refresh_from_db();self.assertEqual(self.batch.execution_intent['state'],'stopped');self.assertIsNone(self.batch.confirmed_at)
        self.authorize()
        # Freshly approved plans queue immediately; revoke only before confirmation.
        self.assertEqual(self.client.post(self.url(),{'action':'revoke'}).status_code,200)
    def test_revoke_while_waiting_and_plan_changes_stop_intent(self):
        self.preview();self.authorize()
        self.assertEqual(self.client.post(self.url(),{'action':'revoke'}).status_code,302)
        self.approve();self.batch.refresh_from_db();self.assertIsNone(self.batch.confirmed_at)
        self.item.status='cleanup_review';self.item.save();self.authorize()
        self.item.before['display_name']='Changed';self.item.save()
        with patch.object(d,'approvals_valid',return_value={}): plan.maybe_schedule(self.batch.pk)
        self.batch.refresh_from_db();self.assertEqual(self.batch.execution_intent['state'],'stopped')
    def test_lost_executor_permission_stops_authorization(self):
        self.preview();self.authorize()
        self.user.is_active=False;self.user.save()
        plan.maybe_schedule(self.batch.pk)
        self.batch.refresh_from_db();self.assertEqual(self.batch.execution_intent['state'],'stopped')
        self.assertIsNone(self.batch.confirmed_at)
    def test_expired_verification_is_refreshed_before_automatic_confirmation(self):
        self.preview();self.approve()
        self.item.verified_at=timezone.now()-timedelta(minutes=11);self.item.save()
        self.authorize();self.batch.refresh_from_db();self.assertEqual(self.batch.status,'preview_queued')
        self.batch.status='checking';self.batch.save()
        with self.live(): d.run(self.batch.pk)
        self.batch.refresh_from_db();self.assertEqual(self.batch.status,'queued')
        self.nsx.http.request.assert_not_called()
    def test_revocation_during_verification_is_not_overwritten_by_worker(self):
        self.preview();self.authorize()
        self.batch.refresh_from_db();self.batch.status='checking';self.batch.save()
        def revoke(item, execute=False, **kwargs):
            current=DeletionBatch.objects.get(pk=self.batch.pk)
            current.execution_intent={**current.execution_intent,'state':'revoked'}
            current.save(update_fields=['execution_intent'])
        with patch.object(d,'process_item',side_effect=revoke): d.run(self.batch.pk)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.execution_intent['state'],'revoked')
        self.assertIsNone(self.batch.confirmed_at)

    def test_original_authorization_deadline_applies_at_execution(self):
        self.preview();self.approve();self.authorize();self.batch.refresh_from_db();self.item.refresh_from_db()
        self.batch.execution_intent['expires_at']=(timezone.now()-timedelta(seconds=1)).isoformat();self.item.batch=self.batch
        with patch.object(d,'approvals_valid',return_value={}):
            with self.assertRaisesMessage(ValidationError,'authorization expired'): d.execution_authorized(self.batch,self.item)
    def test_performance_report_is_read_only_and_omits_object_data(self):
        self.item.finished_at=timezone.now();self.item.verification={'timings':{'phase':'preview','total_seconds':5,'dependency_seconds':4,'shared_preview_scan':True}};self.item.save()
        output=StringIO();call_command('deletion_performance',days=7,json=True,stdout=output)
        report=json.loads(output.getvalue());self.assertEqual(report['preview']['samples'],1)
        self.assertEqual(report['shared_preview_samples'],1);self.assertNotIn(self.item.path,output.getvalue())

class ParallelPreviewTests(TransactionTestCase):
    def test_read_only_checks_are_bounded_and_execution_is_serial(self):
        from django.db import connection
        if connection.vendor!='postgresql': self.skipTest('PostgreSQL worker concurrency')
        import threading,time
        from .models import Environment
        env=Environment.objects.create(name='Parallel',slug='parallel',manager='https://nsx.example')
        batch=DeletionBatch.objects.create(environment=env,manager=env.manager,status='checking')
        for i in range(6): DeletionItem.objects.create(batch=batch,path=f'/infra/services/{i}',name=str(i),revision=1)
        active=0;peak=0;lock=threading.Lock();cache_ids=set()
        def process(item,execute=False,preview_cache=None):
            nonlocal active,peak
            with lock: active+=1;peak=max(peak,active);cache_ids.add(id(preview_cache))
            time.sleep(.05)
            with lock: active-=1
        with patch.object(d,'process_item',side_effect=process): d.run(batch.pk)
        self.assertGreater(peak,1);self.assertLessEqual(peak,3);self.assertEqual(len(cache_ids),1)
        batch.refresh_from_db();batch.status='running';batch.save();batch.items.update(status='verified');peak=0
        with patch.object(d,'process_item',side_effect=process): d.run(batch.pk)
        self.assertEqual(peak,1)
