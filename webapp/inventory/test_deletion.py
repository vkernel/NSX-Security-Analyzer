from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import deletion as d
from .models import Environment, Finding, DeletionBatch, DeletionItem
from .finding_workflow import review_fingerprint


class DeleteTransportTests(SimpleTestCase):
    def test_allowlist_and_no_retry_redirect_or_force(self):
        client = Mock(base_url='https://nsx.example/policy/api/v1', timeout=10, headers={})
        client.http.request.return_value.status = 200
        self.assertEqual(d.delete_once(client, '/infra/domains/default/groups/test'), 200)
        args, kwargs = client.http.request.call_args
        self.assertEqual(args, ('DELETE','https://nsx.example/policy/api/v1/infra/domains/default/groups/test?fail_if_subtree_exists=true'))
        self.assertFalse(kwargs['retries']); self.assertFalse(kwargs['redirect'])
        for path in ['/infra/domains/a/groups/../b', '/infra/services/a?force=true', '/infra/services/%2e', '/infra//services/a', '/infra/realized-state/virtual-machines/a', '/global-infra/services/a']:
            with self.assertRaises(ValidationError): d.target_type(path)


@override_settings(STORAGES={'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DeletionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('delete-admin', password='test-only')
        self.env = Environment.objects.create(name='Delete test', slug='delete-test', manager='https://nsx.example')
        self.obj = {'resource_type':'Service','path':'/infra/services/test','id':'test','unique_id':'identity',
                    '_create_time':12,'_revision':1,'service_entries':[]}
        self.evidence = {'configuration':d.clean(self.obj),'unique_id':'identity','created_at':12,'usage':'unused_candidate'}
        self.finding = Finding.objects.create(environment=self.env, kind='unused_service', path=self.obj['path'], name='Test',
            owner=self.user, workflow_state='ready', qualification='eligible', required_approvals=1, evidence=self.evidence,
            first_seen=timezone.now(), last_seen=timezone.now(), evaluated_at=timezone.now(),
            approvals={'owner':{'actor_id':str(self.user.pk),'fingerprint':review_fingerprint('unused_service',self.evidence)}})
        self.batch = DeletionBatch.objects.create(environment=self.env, requested_by=self.user, manager=self.env.manager,
            status='preview_ready', checked_at=timezone.now(), confirmed_at=timezone.now(), ticket='CHG-1')
        self.item = DeletionItem.objects.create(batch=self.batch, finding=self.finding, path=self.finding.path, name='Test',
            revision=self.finding.revision, status='verified', before=self.obj, approved_evidence=self.evidence, approvals=self.finding.approvals)
        self.client.force_login(self.user)

    def test_approval_validation_requires_valid_current_decisions(self):
        with patch.object(d, 'approval_readiness', return_value={'status':'ready','coverage':{}}), patch.object(d,'coverage_changed',return_value=False):
            d.approvals_valid(self.finding)
            self.finding.required_approvals=2
            with self.assertRaises(ValidationError): d.approvals_valid(self.finding)
            self.finding.approvals['second']=deepcopy(self.finding.approvals['owner'])
            with self.assertRaises(ValidationError): d.approvals_valid(self.finding)
            self.finding.required_approvals=1; self.finding.evidence={'changed':True}
            with self.assertRaises(ValidationError): d.approvals_valid(self.finding)

    def test_preview_permission_dedup_and_confirmation(self):
        operator=get_user_model().objects.create_user('operator',is_staff=True)
        self.client.force_login(operator)
        self.assertEqual(self.client.post(reverse('deletion-selection', args=[self.env.pk]), {'finding':self.finding.pk}).status_code,403)
        self.client.force_login(self.user)
        with patch.object(d,'delete_once') as delete:
            response=self.client.post(reverse('deletion-selection',args=[self.env.pk]),{'finding':[self.finding.pk,self.finding.pk]})
            self.assertEqual(response.status_code,302)
            new=DeletionBatch.objects.exclude(pk=self.batch.pk).get()
            self.assertEqual(new.items.count(),1); self.assertEqual(new.status,'preview_queued')
            self.assertContains(self.client.get(reverse('deletion-detail',args=[self.batch.pk])), 'Permanently delete 1 object')
            self.client.post(reverse('deletion-detail',args=[self.batch.pk]), {'ticket':'CHG-1'})
            self.batch.refresh_from_db(); self.assertEqual(self.batch.status,'preview_ready')
            response=self.client.post(reverse('deletion-detail',args=[self.batch.pk]), {'ticket':'CHG-1','confirm':'yes'})
            self.assertEqual(response.status_code,302)
            self.batch.refresh_from_db(); self.assertEqual(self.batch.status,'queued')
            self.client.post(reverse('deletion-detail',args=[self.batch.pk]), {'ticket':'CHG-1','confirm':'yes'})
            self.assertEqual(DeletionBatch.objects.filter(pk=self.batch.pk,status='queued').count(),1)
            delete.assert_not_called()

    def test_live_checks_block_missing_identity_changed_configuration_and_references(self):
        client=Mock(inventory_warnings=[]); client.get.return_value=self.obj
        with patch.object(d,'approvals_valid'), patch.object(d.collector,'objects',return_value=[]), \
             patch.object(d.collector,'verified_configuration_search',return_value=([self.obj],{'mode':'all_types'})) as search:
            self.assertEqual(d.validate_live(client,self.finding),self.obj)
            client.inventory_warnings=[{'endpoint':'/infra/services'}]
            with self.assertRaisesMessage(ValidationError,'inventory changed'): d.validate_live(client,self.finding)
            client.inventory_warnings=[]
            client.get.return_value=dict(self.obj,display_name='Changed')
            with self.assertRaisesMessage(ValidationError,'Configuration differs'): d.validate_live(client,self.finding)
            client.get.return_value=dict(self.obj,unique_id='recreated')
            with self.assertRaisesMessage(ValidationError,'identity changed'): d.validate_live(client,self.finding)
            client.get.return_value=dict(self.obj,_system_owned=True)
            with self.assertRaisesMessage(ValidationError,'Protected'): d.validate_live(client,self.finding)
            client.get.return_value=self.obj
            search.return_value=([self.obj],{'mode':'explicit_types','resource_types':['Service']})
            with self.assertRaisesMessage(ValidationError,'coverage is incomplete'): d.validate_live(client,self.finding)
            search.return_value=([self.obj, {'path':'/infra/rule','services':[self.obj['path']]}],{'mode':'all_types'})
            with self.assertRaisesMessage(ValidationError,'Referenced by'): d.validate_live(client,self.finding)

    def execute_with(self, response=200, after=None, error=None):
        @contextmanager
        def client(env): yield Mock()
        with patch.object(d,'connection',client), patch.object(d,'approvals_valid'), \
             patch.object(d,'validate_live',return_value=self.obj), \
             patch.object(d,'current_object',side_effect=[self.obj,after]), \
             patch.object(d,'delete_once',return_value=response,side_effect=error) as delete:
            d.process_item(self.item,execute=True)
            return delete.call_count

    def test_success_persists_evidence_and_marks_decommissioned(self):
        self.assertEqual(self.execute_with(),1)
        self.item.refresh_from_db();self.finding.refresh_from_db()
        self.assertEqual(self.item.status,'deleted');self.assertEqual(self.finding.workflow_state,'decommissioned')
        self.assertEqual(self.item.before,self.obj);self.assertEqual(self.finding.change_ticket,'CHG-1')
        self.assertTrue(self.finding.events.filter(details__action='nsx_deleted').exists())

    def test_unknown_outcome_never_marks_complete_or_retries(self):
        self.assertEqual(self.execute_with(error=TimeoutError()),1)
        self.item.refresh_from_db();self.finding.refresh_from_db()
        self.assertEqual(self.item.status,'uncertain');self.assertEqual(self.finding.workflow_state,'ready')
        self.assertIsNotNone(self.item.request_sent_at)

    def test_accepted_but_still_present_is_uncertain(self):
        self.execute_with(after=self.obj)
        self.item.refresh_from_db();self.assertEqual(self.item.status,'uncertain')

    def test_expired_preview_and_changed_review_block_delete(self):
        self.batch.checked_at=timezone.now()-timedelta(minutes=11);self.batch.save()
        self.item.batch=self.batch
        self.assertEqual(self.execute_with(),0)
        self.item.refresh_from_db();self.assertEqual(self.item.status,'blocked')
        self.batch.checked_at=timezone.now();self.batch.save()
        self.finding.revision+=1;self.finding.save();self.item.finding=self.finding
        self.assertEqual(self.execute_with(),0)

    def test_already_absent_and_interruption_are_not_success(self):
        @contextmanager
        def client(env): yield Mock()
        with patch.object(d,'connection',client), patch.object(d,'approvals_valid'), patch.object(d,'validate_live',return_value=None), patch.object(d,'delete_once') as delete:
            d.process_item(self.item,execute=True)
            delete.assert_not_called()
        self.item.refresh_from_db();self.assertEqual(self.item.status,'already_absent')
        self.finding.refresh_from_db();self.assertEqual(self.finding.workflow_state,'ready')
        self.batch.status='running';self.batch.save()
        self.item.status='sending';self.item.save()
        d.interrupted(self.batch.pk)
        self.item.refresh_from_db();self.assertEqual(self.item.status,'uncertain')
        self.assertIsNone(d.claim())

    def test_resource_specific_live_condition_checks(self):
        cases = [('empty_group','Group','/infra/domains/default/groups/g'),
                 ('disabled','Rule','/infra/domains/default/security-policies/p/rules/r'),
                 ('zero_hits','Rule','/infra/domains/default/security-policies/p/rules/r'),
                 ('empty_policy','SecurityPolicy','/infra/domains/default/security-policies/p')]
        for finding_kind, resource_type, path in cases:
            with self.subTest(kind=finding_kind):
                obj={'resource_type':resource_type,'path':path,'unique_id':'identity','_create_time':12,'disabled':True}
                parent={'scope':['ANY'],'category':'Application','sequence_number':1,'stateful':True}
                config={'rule':d.clean(obj),'policy':parent} if resource_type=='Rule' else d.clean(obj)
                self.finding.kind=finding_kind; self.finding.path=path
                self.finding.evidence=dict(self.evidence,configuration=config)
                client=Mock(inventory_warnings=[]);client.get.side_effect=lambda p: obj if p==path else parent
                with patch.object(d,'approvals_valid'), patch.object(d.collector,'objects',return_value=[]) as objects, \
                     patch.object(d.collector,'verified_configuration_search',return_value=([obj],{'mode':'all_types'})), \
                     patch.object(d.collector,'membership',return_value=('empty',[])) as members, \
                     patch.object(d.collector,'rule_statistics',return_value={'hit_status':'zero_hits','hit_count':0}) as stats:
                    self.assertEqual(d.validate_live(client,self.finding),obj)
                    if finding_kind=='empty_group':
                        members.return_value=('unknown',[])
                        with self.assertRaises(ValidationError): d.validate_live(client,self.finding)
                    if finding_kind=='zero_hits':
                        stats.return_value={'hit_status':'traffic_recorded','hit_count':1}
                        with self.assertRaises(ValidationError): d.validate_live(client,self.finding)
                    if finding_kind=='empty_policy':
                        objects.side_effect=lambda c,p: [{'path':path+'/rules/new'}] if p==path+'/rules' else []
                        with self.assertRaises(ValidationError): d.validate_live(client,self.finding)

    def test_failed_delete_and_duplicate_execution(self):
        self.assertEqual(self.execute_with(response=403),1)
        self.item.refresh_from_db(); self.finding.refresh_from_db()
        self.assertEqual(self.item.status,'failed'); self.assertEqual(self.finding.workflow_state,'ready')
        self.assertEqual(self.execute_with(),0)

    def test_revoked_admin_blocks_execution(self):
        self.user.is_superuser=False; self.user.save()
        self.assertEqual(self.execute_with(),0)
        self.item.refresh_from_db(); self.assertEqual(self.item.status,'blocked')

    def test_service_impact_and_partial_batch(self):
        from .impact import preview
        result=preview({'inventory':{'services':[{'path':self.finding.path,'name':'Test','configuration':self.obj}]}},self.finding.path)
        self.assertEqual(result['root']['impact_kind'],'service')
        second=DeletionItem.objects.create(batch=self.batch,path='/infra/services/blocked',name='Blocked',revision=0,status='blocked')
        self.batch.status='running';self.batch.save()
        with patch.object(d,'process_item') as process:
            d.run(self.batch.pk)
            self.assertEqual(process.call_count,1)
            self.assertEqual(process.call_args.args[0].pk,self.item.pk)
        self.batch.refresh_from_db();self.assertEqual(self.batch.status,'completed')
        second.refresh_from_db();self.assertEqual(second.status,'blocked')
