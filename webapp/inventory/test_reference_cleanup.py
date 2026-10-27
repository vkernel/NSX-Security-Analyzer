from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import json
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import deletion as d, reference_cleanup as cleanup
from .models import WorkspacePolicy, DeletionItem
from . import test_deletion


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ReferenceCleanupTests(TestCase):
    def setUp(self):
        test_deletion.DeletionTests.setUp(self)
        WorkspacePolicy.objects.update_or_create(pk=1, defaults={'required_approvals': 1})
        self.group = dict(self.obj, resource_type='Group', path='/infra/domains/default/groups/empty', id='empty')
        self.group.pop('service_entries')
        self.other = '/infra/domains/default/groups/remaining'
        self.parent = {'resource_type': 'SecurityPolicy', 'path': '/infra/domains/default/security-policies/p',
                       'id': 'p', 'display_name': 'Policy', '_revision': 2, 'scope': ['ANY'], 'category': 'Application'}
        self.rule = {'resource_type': 'Rule', 'path': self.parent['path'] + '/rules/r', 'id': 'r',
                     'display_name': 'Rule', 'unique_id': 'rule-identity', '_create_time': 10, '_revision': 4,
                     'source_groups': [self.group['path'], self.other], 'destination_groups': [self.other],
                     'scope': ['ANY'], 'action': 'ALLOW'}
        self.finding.kind = 'empty_group'; self.finding.path = self.group['path']
        self.finding.evidence = {'unique_id': self.group['unique_id'], 'created_at': self.group['_create_time'],
                                 'configuration': d.clean(self.group)}
        self.finding.save()
        self.item.path = self.group['path']; self.item.before = self.group; self.item.status = 'pending'; self.item.save()
        self.batch.confirmed_at = None; self.batch.ticket = ''; self.batch.save()
        self.state = {v['path']: deepcopy(v) for v in (self.group, self.rule, self.parent)}
        self.nsx = Mock(inventory_warnings=[], base_url='https://nsx.example/policy/api/v1', headers={}, timeout=10)
        def get(path, *args):
            if path not in self.state: raise d.collector.AuditError('missing', status_code=404)
            return deepcopy(self.state[path])
        self.nsx.get.side_effect = get
        def put(method, url, **kw):
            self.assertEqual(method, 'PUT')
            payload = json.loads(kw['body'])
            current = self.state[self.rule['path']]
            self.assertEqual(payload['_revision'], current['_revision'])
            self.assertFalse(kw['retries']); self.assertFalse(kw['redirect'])
            self.state[self.rule['path']].update(payload)
            self.state[self.rule['path']]['_revision'] += 1
            return Mock(status=200)
        self.nsx.http.request.side_effect = put

    @contextmanager
    def live(self):
        @contextmanager
        def connection(env): yield self.nsx
        def search(*args):
            return deepcopy(list(self.state.values())), {'mode': 'all_types', 'status': 'verified'}
        with patch.object(d, 'connection', connection), patch.object(d, 'approvals_valid', return_value={}), \
             patch.object(d.collector, 'objects', return_value=[]), \
             patch.object(d.collector, 'membership', return_value=('empty', {})), \
             patch.object(d.collector, 'verified_configuration_search', side_effect=search):
            yield

    def preview(self):
        with self.live(): d.process_item(self.item)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'cleanup_review', self.item.message)

    def approve(self, user=None):
        if user: self.client.force_login(user)
        with patch.object(d, 'approvals_valid', return_value={}):
            response = self.client.post(reverse('deletion-reference-cleanup', args=[self.item.pk]),
                {'approve': 'yes', 'fingerprint': cleanup.fingerprint(self.item)})
        self.item.refresh_from_db()
        return response

    def confirm(self):
        self.batch.confirmed_at = timezone.now(); self.batch.ticket = 'CHG-cleanup'; self.batch.save()
        self.item.batch = self.batch

    def test_preview_proposes_only_reference_removal_without_mutating(self):
        self.preview()
        self.nsx.http.request.assert_not_called()
        change = self.item.cleanup_plan[0]
        self.assertEqual(change['fields'][0]['after'], [self.other])
        self.assertEqual(change['before'], self.rule)
        self.assertEqual(change['after']['action'], 'ALLOW')
        self.assertEqual(self.item.verification['Dependencies'], 'References found; separate firewall-change approval required')
        response = self.client.get(reverse('deletion-reference-cleanup', args=[self.item.pk]))
        self.assertContains(response, 'Modify 1 firewall rule')
        self.assertContains(response, 'Policy: Policy')
        self.assertContains(response, self.other)
        self.assertContains(self.client.get(reverse('deletion-detail', args=[self.batch.pk])), 'Reference cleanup required')

    def test_last_group_exclusions_scope_and_non_rule_references_are_blocked(self):
        variants = [({'source_groups': [self.group['path']]}, 'Last group'),
                    ({'sources_excluded': True}, 'Negated'),
                    ({'destinations_excluded': True}, 'Negated'),
                    ({'scope': [self.group['path']]}, 'Applied To'),
                    ({'source_groups': [self.group['path'], 'ANY']}, 'ANY'),
                    ({'_protection': 'REQUIRE_OVERRIDE'}, 'protected'),
                    ({'remote_path': '/global-infra/rule'}, 'protected'),
                    ({'_revision': None}, 'revision')]
        for changes, message in variants:
            with self.subTest(changes=changes):
                self.state[self.rule['path']] = dict(self.rule, **changes)
                with self.assertRaisesMessage(ValidationError, message):
                    cleanup.build_plan(self.nsx, self.group, {self.rule['path']})
        with self.assertRaisesMessage(ValidationError, 'not a supported'):
            cleanup.build_plan(self.nsx, self.group, {'/infra/services/dependency'})
        self.nsx.http.request.assert_not_called()

    def test_both_source_and_destination_are_explicitly_planned(self):
        self.state[self.rule['path']]['destination_groups'] = [self.other, self.group['path']]
        self.preview()
        self.assertEqual(len(self.item.cleanup_plan[0]['fields']), 2)

    def test_nonempty_group_never_gets_cleanup_plan(self):
        with self.live(), patch.object(d.collector, 'membership', return_value=('nonempty', {})):
            d.process_item(self.item)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'blocked'); self.assertEqual(self.item.cleanup_plan, [])

    def test_stricter_firewall_setting_requires_two_distinct_approvals(self):
        WorkspacePolicy.objects.filter(pk=1).update(rules_approvals=2)
        self.preview()
        self.assertEqual(self.approve().status_code, 302)
        self.assertEqual(self.item.status, 'cleanup_review')
        self.assertContains(self.approve(), 'already approved')
        second = get_user_model().objects.create_superuser('second-cleanup', password='test')
        self.assertEqual(self.approve(second).status_code, 302)
        self.assertEqual(self.item.status, 'verified')
        cleanup.validate_approvals(self.item)
        self.assertEqual(len(self.item.cleanup_approvals), 2)

    def test_approval_fingerprint_expiry_and_permission_guards(self):
        self.preview()
        url = reverse('deletion-reference-cleanup', args=[self.item.pk])
        with patch.object(d, 'approvals_valid', return_value={}):
            self.assertContains(self.client.post(url, {'approve': 'yes', 'fingerprint': 'wrong'}), 'explicitly approve')
        self.item.verified_at = timezone.now() - timedelta(minutes=11); self.item.save()
        self.assertContains(self.approve(), 'expired')
        user = get_user_model().objects.create_user('operator', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'approve': 'yes'}).status_code, 403)

    def test_group_approval_does_not_authorize_cleanup_and_confirmation_is_explicit(self):
        self.preview()
        with self.assertRaises(ValidationError): cleanup.validate_approvals(self.item)
        self.approve()
        url = reverse('deletion-detail', args=[self.batch.pk])
        self.assertContains(self.client.post(url, {'ticket': 'CHG', 'confirm': 'yes'}), 'Explicitly confirm the firewall')
        self.assertEqual(self.client.post(url, {'ticket': 'CHG', 'confirm': 'yes', 'confirm_cleanup': 'yes'}).status_code, 302)
        self.batch.refresh_from_db(); self.assertEqual(self.batch.status, 'queued')

    def test_changed_plan_or_revoked_reviewer_blocks_approved_execution(self):
        self.preview(); self.approve()
        self.item.cleanup_plan[0]['after']['action'] = 'DROP'
        with self.assertRaisesMessage(ValidationError, 'plan changed'): cleanup.validate_approvals(self.item)
        self.item.refresh_from_db()
        self.user.is_active = False; self.user.save()
        with self.assertRaisesMessage(ValidationError, 'no longer authorized'): cleanup.validate_approvals(self.item)

    def test_policy_requirement_increase_invalidates_single_approval(self):
        self.preview(); self.approve()
        WorkspacePolicy.objects.filter(pk=1).update(groups_approvals=2)
        with self.assertRaisesMessage(ValidationError, '2 distinct'): cleanup.validate_approvals(self.item)

    def test_success_updates_rule_then_deletes_and_records_history(self):
        self.preview(); self.approve(); self.confirm()
        def delete(client, path):
            self.assertEqual(self.state[self.rule['path']]['source_groups'], [self.other])
            self.state.pop(path)
            return 200
        with self.live(), patch.object(d, 'delete_once', side_effect=delete) as delete_call:
            d.process_item(self.item, execute=True)
        self.item.refresh_from_db(); self.finding.refresh_from_db()
        self.assertEqual(self.item.status, 'deleted', self.item.message)
        self.assertEqual(self.finding.workflow_state, 'decommissioned')
        self.assertEqual(self.item.cleanup_results[0]['status'], 'updated')
        self.assertEqual(self.item.cleanup_plan[0]['before']['source_groups'], self.rule['source_groups'])
        self.assertEqual(self.nsx.http.request.call_count, 1); self.assertEqual(delete_call.call_count, 1)
        self.assertContains(self.client.get(reverse('deletion-history', args=[self.env.pk])), 'View rule cleanup and outcomes')

    def test_changed_rule_and_new_reference_prevent_all_mutations(self):
        self.preview(); self.approve(); self.confirm()
        self.state[self.rule['path']]['_revision'] += 1
        with self.live(), patch.object(d, 'delete_once') as delete:
            d.process_item(self.item, execute=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'blocked'); self.assertIn('changed', self.item.message)
        self.nsx.http.request.assert_not_called(); delete.assert_not_called()

    def test_failed_put_does_not_delete_or_retry(self):
        self.preview(); self.approve(); self.confirm()
        self.nsx.http.request.side_effect = None; self.nsx.http.request.return_value.status = 412
        with self.live(), patch.object(d, 'delete_once') as delete:
            d.process_item(self.item, execute=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'blocked', self.item.message)
        self.assertEqual(self.item.cleanup_results[0]['status'], 'failed')
        self.assertEqual(self.nsx.http.request.call_count, 1); delete.assert_not_called()

    def test_timeout_is_uncertain_with_durable_rule_intent(self):
        self.preview(); self.approve(); self.confirm()
        self.nsx.http.request.side_effect = TimeoutError()
        with self.live(), patch.object(d, 'delete_once') as delete:
            d.process_item(self.item, execute=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'uncertain')
        self.assertEqual(self.item.cleanup_results[0]['status'], 'uncertain')
        delete.assert_not_called()

    def test_partial_update_followed_by_search_failure_keeps_history_and_group(self):
        self.preview(); self.approve(); self.confirm()
        calls = 0
        def search(*args):
            nonlocal calls
            calls += 1
            if calls > 1: raise d.collector.InventoryChanged('changing search')
            return deepcopy(list(self.state.values())), {'mode': 'all_types'}
        with self.live(), patch.object(d.collector, 'verified_configuration_search', side_effect=search), patch.object(d, 'delete_once') as delete:
            d.process_item(self.item, execute=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'blocked', self.item.message)
        self.assertEqual(self.item.cleanup_results[0]['status'], 'updated')
        self.assertIn('1 rule update(s) verified', self.item.message)
        self.assertIn(self.group['path'], self.state); delete.assert_not_called()

    def test_membership_change_before_put_stops_cleanup(self):
        self.preview(); self.approve(); self.confirm()
        with self.live(), patch.object(d.collector, 'membership', side_effect=[('empty', {}), ('nonempty', {})]), patch.object(d, 'delete_once') as delete:
            d.process_item(self.item, execute=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'blocked')
        self.nsx.http.request.assert_not_called(); delete.assert_not_called()

    def test_interrupted_cleanup_is_never_automatically_retried(self):
        self.preview(); self.approve()
        self.item.cleanup_results = [{'path': self.rule['path'], 'status': 'sending'}]; self.item.status = 'validating'; self.item.save()
        self.batch.status = 'running'; self.batch.save()
        d.interrupted(self.batch.pk)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'uncertain')
        self.assertEqual(self.item.cleanup_results[0]['status'], 'sending')

    def test_new_unsupported_dependency_after_approval_blocks_updates(self):
        self.preview(); self.approve(); self.confirm()
        path = '/infra/domains/default/groups/parent'
        self.state[path] = {'resource_type': 'Group', 'path': path, 'expression': [{'paths': [self.group['path']]}]}
        with self.live(), patch.object(d, 'delete_once') as delete:
            d.process_item(self.item, execute=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'blocked')
        self.nsx.http.request.assert_not_called(); delete.assert_not_called()

    def test_membership_change_after_put_prevents_deletion(self):
        self.preview(); self.approve(); self.confirm()
        with self.live(), patch.object(d.collector, 'membership', side_effect=[('empty', {}), ('empty', {}), ('nonempty', {})]), patch.object(d, 'delete_once') as delete:
            d.process_item(self.item, execute=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'blocked')
        self.assertEqual(self.item.cleanup_results[0]['status'], 'updated')
        self.assertEqual(self.nsx.http.request.call_count, 1); delete.assert_not_called()

    def test_second_rule_failure_preserves_first_update_and_originals(self):
        other_rule = dict(self.rule, path=self.parent['path'] + '/rules/second', id='second', unique_id='second-identity')
        self.state[other_rule['path']] = other_rule
        self.preview(); self.approve(); self.confirm()
        first_put = self.nsx.http.request.side_effect
        attempts = 0
        def put(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            return first_put(*args, **kwargs) if attempts == 1 else Mock(status=409)
        self.nsx.http.request.side_effect = put
        with self.live(), patch.object(d, 'delete_once') as delete:
            d.process_item(self.item, execute=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'blocked', self.item.message)
        self.assertEqual([r['status'] for r in self.item.cleanup_results], ['updated', 'failed'])
        self.assertEqual(len(self.item.cleanup_plan), 2)
        self.assertEqual(attempts, 2); delete.assert_not_called()

    def test_update_readback_mismatch_is_uncertain(self):
        self.preview(); self.approve(); self.confirm()
        first_put = self.nsx.http.request.side_effect
        def put(*args, **kwargs):
            response = first_put(*args, **kwargs)
            self.state[self.rule['path']]['action'] = 'DROP'
            return response
        self.nsx.http.request.side_effect = put
        with self.live(), patch.object(d, 'delete_once') as delete:
            d.process_item(self.item, execute=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'uncertain')
        self.assertEqual(self.item.cleanup_results[0]['status'], 'uncertain'); delete.assert_not_called()

    def test_incomplete_search_never_offers_reference_cleanup(self):
        with self.live(), patch.object(d.collector, 'verified_configuration_search', return_value=(list(self.state.values()), {'mode': 'explicit_types'})):
            d.process_item(self.item)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'blocked')
        self.assertEqual(self.item.cleanup_plan, []); self.nsx.http.request.assert_not_called()

    def test_parent_policy_change_after_preview_prevents_updates(self):
        self.preview(); self.approve(); self.confirm()
        self.state[self.parent['path']]['scope'] = [self.other]
        with self.live(), patch.object(d, 'delete_once') as delete:
            d.process_item(self.item, execute=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'blocked')
        self.nsx.http.request.assert_not_called(); delete.assert_not_called()
