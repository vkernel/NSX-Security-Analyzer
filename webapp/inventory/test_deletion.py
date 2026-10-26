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
    def test_inventory_failure_is_not_reported_as_missing_http_status(self):
        message = d.describe_error(d.collector.InventoryChanged('conflicting configuration versions'))
        self.assertIn('Live inventory verification failed', message)
        self.assertIn('conflicting configuration versions', message)
        self.assertNotIn('HTTP unavailable', message)

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
            revision=self.finding.revision, status='verified', verified_at=self.batch.confirmed_at, before=self.obj, approved_evidence=self.evidence, approvals=self.finding.approvals)
        self.client.force_login(self.user)

    def test_history_is_scoped_paginated_and_permission_checked(self):
        other = Environment.objects.create(name='Other', slug='other', manager='https://other.example')
        DeletionBatch.objects.create(environment=other, manager=other.manager, ticket='HIDDEN-TICKET')
        DeletionBatch.objects.bulk_create([
            DeletionBatch(environment=self.env, manager=self.env.manager, requested_by=self.user)
            for _ in range(25)])
        url = reverse('deletion-history', args=[self.env.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['page']), 25)
        self.assertContains(response, 'Page 1 of 2')
        self.assertNotContains(response, 'HIDDEN-TICKET')
        response = self.client.get(url + '?page=2')
        self.assertContains(response, 'CHG-1')
        self.assertEqual(response.context['page'][0].object_count, 1)
        self.client.force_login(get_user_model().objects.create_user('history-reader', is_staff=True))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_progress_fragment_is_read_only_and_stops_at_preview(self):
        self.batch.status='checking'; self.batch.confirmed_at=None; self.batch.save()
        self.item.status='pending'; self.item.save()
        url = reverse('deletion-detail', args=[self.batch.pk]) + '?fragment=1'
        with patch.object(d,'delete_once') as delete, patch.object(d,'connection') as connect:
            response=self.client.get(url)
            self.assertContains(response, 'data-active="true"')
            self.assertContains(response, '0 of 1 objects')
            self.assertNotContains(response, '<html')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            delete.assert_not_called(); connect.assert_not_called()
        self.batch.status='preview_ready'; self.batch.checked_at=timezone.now(); self.batch.save()
        self.item.status='verified'; self.item.verified_at=timezone.now(); self.item.save()
        response=self.client.get(url)
        self.assertContains(response, 'data-active="false"')
        self.assertContains(response, 'Confirm deletion of 1 object')
        self.assertContains(response, 'Change-ticket reference')
        self.batch.refresh_from_db()
        self.assertIsNone(self.batch.confirmed_at)
        self.client.force_login(get_user_model().objects.create_user('progress-reader', is_staff=True))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_expired_confirmation_is_not_offered_and_results_are_clear(self):
        self.batch.confirmed_at=None; self.batch.checked_at=timezone.now()-timedelta(minutes=11); self.batch.save()
        self.item.verified_at=self.batch.checked_at; self.item.save()
        url=reverse('deletion-detail', args=[self.batch.pk])
        response=self.client.get(url)
        self.assertContains(response, 'expired')
        self.assertNotContains(response, 'id="delete-confirm-open"')
        self.batch.status='completed'; self.batch.confirmed_at=timezone.now(); self.batch.save()
        self.item.status='uncertain'; self.item.save()
        response=self.client.get(url)
        self.assertContains(response, '0 deleted · 0 blocked')
        self.assertContains(response, '1 uncertain')
        self.assertContains(response, 'Outcome needs verification')
        self.assertContains(response, 'data-active="false"')

    def test_blocked_preview_retains_live_search_diagnostics(self):
        diagnostics = {'mode':'explicit_types', 'query':'resource_type:Group',
                       'rejected_queries':[{'query':'*', 'error':'No results'}],
                       'pagination_attempts': [{'attempt':1, 'duplicates_by_resource_type': {'VirtualMachine':18}, 'unique_count':37358, 'reported_total':37376}],
                       'headers': {'Authorization':'must-not-be-saved'}}
        client = Mock(search_diagnostics=diagnostics)
        with patch.object(d,'guard'), patch.object(d,'connection') as connect, patch.object(d,'validate_live',side_effect=ValidationError('Live dependency coverage is incomplete.')), patch.object(d,'delete_once') as delete:
            connect.return_value.__enter__.return_value = client
            d.process_item(self.item)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'blocked')
        self.assertEqual(self.item.verification['search_diagnostics']['query'], diagnostics['query'])
        self.assertNotIn('headers', self.item.verification['search_diagnostics'])
        self.assertEqual(self.item.verification['search_diagnostics']['pagination_attempts'], diagnostics['pagination_attempts'])
        response = self.client.get(reverse('deletion-detail',args=[self.batch.pk]))
        self.assertContains(response, 'Live dependency-search diagnostics')
        self.assertContains(response, 'No results')
        delete.assert_not_called()

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
        self.item.verified_at=timezone.now()-timedelta(minutes=11);self.item.save()
        self.item.batch=self.batch
        self.assertEqual(self.execute_with(),0)
        self.item.refresh_from_db();self.assertEqual(self.item.status,'blocked')
        self.batch.checked_at=timezone.now();self.batch.save()
        self.finding.revision+=1;self.finding.save();self.item.finding=self.finding
        self.assertEqual(self.execute_with(),0)

    def test_timely_confirmation_survives_queue_wait(self):
        self.batch.confirmed_at=timezone.now()-timedelta(minutes=30)
        self.batch.save()
        self.item.verified_at=self.batch.confirmed_at-timedelta(minutes=2)
        self.item.save()
        self.assertEqual(self.execute_with(),1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status,'deleted')

    def test_expired_execution_authorization_blocks(self):
        self.batch.confirmed_at=timezone.now()-timedelta(minutes=61)
        self.batch.save()
        self.item.verified_at=self.batch.confirmed_at
        self.item.save()
        self.assertEqual(self.execute_with(),0)
        self.item.refresh_from_db()
        self.assertIn('Execution authorization expired',self.item.message)

    def test_authorization_expiring_during_final_read_blocks(self):
        def final_read(*args):
            self.batch.confirmed_at=timezone.now()-timedelta(hours=2)
            self.item.verified_at=self.batch.confirmed_at
            return self.obj
        with patch.object(d,'connection') as connect, patch.object(d,'approvals_valid'), \
             patch.object(d,'validate_live',return_value=self.obj), \
             patch.object(d,'current_object',side_effect=final_read), patch.object(d,'delete_once') as delete:
            d.process_item(self.item,execute=True)
            delete.assert_not_called()
        self.item.refresh_from_db()
        self.assertIn('Execution authorization expired',self.item.message)

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

    def test_saved_configuration_read_only_and_download(self):
        self.item.status = 'uncertain'
        self.item.before['description'] = '<script>unsafe</script>'
        self.item.save()
        url = reverse('deletion-configuration', args=[self.item.pk])
        with patch.object(d, 'connection') as connection, patch.object(d, 'delete_once') as delete:
            response = self.client.get(url)
            self.assertContains(response, 'Saved before deletion')
            self.assertContains(response, 'Check whether the object still exists')
            self.assertNotContains(response, '<script>unsafe</script>')
            download = self.client.get(url + '?download=1')
            self.assertEqual(download.json(), self.item.before)
            self.assertIn('attachment;', download['Content-Disposition'])
            self.assertEqual(download['Cache-Control'], 'private, no-store')
            self.assertEqual(self.client.post(url).status_code, 405)
            connection.assert_not_called(); delete.assert_not_called()
        self.item.finding = None
        self.item.save()
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(get_user_model().objects.create_user('history-viewer'))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url + '?download=1').status_code, 403)

    def test_saved_group_configuration_and_missing_backup(self):
        self.item.before = {'resource_type':'Group', 'description':'VDI group',
                            'tags':[{'scope':'role','tag':'vdi'}], 'expression':[{'resource_type':'Condition','value':'VDI'}]}
        self.item.save()
        url = reverse('deletion-configuration', args=[self.item.pk])
        response = self.client.get(url)
        self.assertContains(response, 'Group definition')
        self.assertContains(response, 'Membership expressions')
        self.assertContains(response, 'VDI automation')
        self.item.before = {}; self.item.save()
        self.assertContains(self.client.get(url), 'No pre-deletion configuration')
        self.assertEqual(self.client.get(url + '?download=1').status_code, 404)

    def test_selected_preview_rejects_changed_revision(self):
        url = reverse('deletion-selection', args=[self.env.pk])
        response = self.client.post(url, {'selected':f'{self.finding.pk}:{self.finding.revision+1}'})
        self.assertContains(response, 'Some selected findings changed')
        self.assertEqual(DeletionBatch.objects.count(), 1)
        response = self.client.post(url, {'selected':f'{self.finding.pk}:{self.finding.revision}'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DeletionBatch.objects.count(), 2)

    def test_preview_explains_current_inventory_blocker(self):
        from .models import Snapshot
        Snapshot.objects.create(environment=self.env, generated_at=timezone.now(),
            summary={'inventory_warnings':True}, report={'inventory_warnings':[{'endpoint':'/vm','detail':'Missing one VM'}]})
        self.item.status = 'blocked'; self.item.save()
        response = self.client.get(reverse('deletion-detail',args=[self.batch.pk]))
        self.assertContains(response, 'Missing one VM')
        self.assertContains(response, 'No selected objects passed verification')
        self.assertContains(response, 'Existing blocked results are historical')


    def test_finding_selection_has_contextual_delete_action(self):
        response = self.client.get(reverse('findings', args=[self.env.pk]) + '?review=ready')
        self.assertContains(response, 'id="finding-selection-bar" hidden')
        self.assertContains(response, 'id="delete-selected-open"')
        self.assertContains(response, 'data-delete-eligible="true"')
        self.assertContains(response, 'id="bulk-delete-preview"')
        self.assertNotContains(response, 'Delete selected from NSX — choose ready objects')
        self.client.force_login(get_user_model().objects.create_user('selection-operator', is_staff=True))
        response = self.client.get(reverse('findings', args=[self.env.pk]) + '?review=ready')
        self.assertNotContains(response, 'id="delete-selected-open"')

    def test_confirmation_rejects_expired_or_legacy_item_even_when_batch_is_fresh(self):
        url = reverse('deletion-detail',args=[self.batch.pk])
        for stamp in (None, timezone.now()-timedelta(minutes=11)):
            self.item.verified_at=stamp; self.item.save()
            response=self.client.post(url,{'ticket':'CHG-expiry','confirm':'yes'})
            self.assertContains(response,'One or more object previews expired')
            self.batch.refresh_from_db()
            self.assertEqual(self.batch.status,'preview_ready')

    def test_live_checks_cannot_substitute_for_explicit_execution_authorization(self):
        self.batch.confirmed_at=None; self.batch.save()
        self.assertEqual(self.execute_with(),0)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status,'blocked')
        self.assertIn('Explicit confirmation',self.item.message)

    def test_preview_saves_check_results_without_deleting(self):
        @contextmanager
        def client(env): yield Mock()
        with patch.object(d,'connection',client), patch.object(d,'guard'), patch.object(d,'validate_live',return_value=self.obj), patch.object(d,'delete_once') as delete:
            d.process_item(self.item,execute=False)
            delete.assert_not_called()
        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.verified_at)
        self.assertIn('Dependencies',self.item.verification)
        self.assertIn('Ready for confirmation',self.item.message)

    def test_expiry_during_live_verification_prevents_delete(self):
        @contextmanager
        def client(env): yield Mock()
        def slow_check(client, finding):
            self.batch.confirmed_at=timezone.now()-timedelta(hours=2)
            self.item.verified_at=self.batch.confirmed_at
            return self.obj
        with patch.object(d,'connection',client), patch.object(d,'guard'), patch.object(d,'validate_live',side_effect=slow_check), patch.object(d,'delete_once') as delete:
            d.process_item(self.item,execute=True)
            delete.assert_not_called()
        self.item.refresh_from_db()
        self.assertEqual(self.item.status,'blocked')
        self.assertIn('Execution authorization expired',self.item.message)

    def test_direct_group_execution_still_requires_membership_and_reference_coverage(self):
        from .deletion_readiness import strict_group_empty
        obj={'resource_type':'Group','path':'/infra/domains/default/groups/direct','unique_id':'identity','_create_time':12,
             'expression':[{'resource_type':'Condition','member_type':'VirtualMachine','key':'Name','operator':'EQUALS','value':'test'}]}
        self.finding.kind='empty_group'; self.finding.path=obj['path']
        self.finding.evidence=dict(self.evidence,configuration=d.clean(obj))
        page={'results':[],'result_count':0}
        client=Mock(inventory_warnings=[])
        client.get.side_effect=lambda path,*args: obj if path==obj['path'] else page
        with patch.object(d,'approvals_valid',return_value={'requires_direct_membership':True}), \
             patch.object(d.collector,'objects',return_value=[]), \
             patch.object(d.collector,'verified_configuration_search',return_value=([obj],{'mode':'all_types'})) as search, \
             patch.object(d.collector,'membership',side_effect=AssertionError('No fast or duplicate probes')):
            self.assertEqual(d.validate_live(client,self.finding),obj)
            self.assertTrue(client.direct_membership_verified)
            page.pop('result_count')
            with self.assertRaises(ValidationError): d.validate_live(client,self.finding)
            page['result_count']=0
            search.return_value=([obj],{'mode':'explicit_types'})
            with self.assertRaises(ValidationError): d.validate_live(client,self.finding)
