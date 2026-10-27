from copy import deepcopy
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from . import deletion as d, execution_policy as ep
from .models import WorkspacePolicy, DeletionBatch, DeletionItem
from .test_deletion import DeletionTests


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ExecutionPolicyTests(TestCase):
    def setUp(self):
        DeletionTests.setUp(self)
        self.second = get_user_model().objects.create_superuser('second', password='test-only')
        self.requester = get_user_model().objects.create_superuser('requester', password='test-only')
        self.batch.requested_by = self.requester
        self.batch.confirmed_at = None
        self.batch.save()
        self.finding.required_approvals = 2
        self.finding.approvals['second'] = dict(self.finding.approvals['owner'], actor_id=str(self.second.pk))
        self.finding.save()
        self.settings = WorkspacePolicy.objects.create(pk=1)
        self.valid = patch.object(d, 'approvals_valid', return_value={})
        self.valid.start(); self.addCleanup(self.valid.stop)

    def test_modes_overrides_and_revoked_executor(self):
        self.assertEqual(ep.eligible_ids(self.item), {self.requester.pk})
        self.settings.execution_mode = 'approvers'; self.settings.save()
        self.assertEqual(ep.eligible_ids(self.item), {self.user.pk, self.second.pk})
        self.settings.services_execution = 'participants'; self.settings.save()
        self.assertEqual(ep.eligible_ids(self.item), {self.user.pk, self.second.pk, self.requester.pk})
        self.second.is_active = False; self.second.save()
        self.assertNotIn(self.second.pk, ep.eligible_ids(self.item))
        with patch.object(d, 'approvals_valid', side_effect=ValidationError('changed evidence')):
            self.assertFalse(ep.can_execute(self.user, self.item))

    def test_cleanup_requires_intersection_and_exact_plan_approvers(self):
        self.finding.kind = 'empty_group'; self.finding.save()
        self.item.cleanup_plan = [{'path': '/rule'}]
        self.item.cleanup_approvals = [{'actor_id': str(self.second.pk)}]
        self.settings.groups_execution = 'participants'; self.settings.rules_execution = 'approvers'; self.settings.save()
        with patch('inventory.reference_cleanup.validate_approvals'):
            self.assertEqual(ep.eligible_ids(self.item), {self.second.pk})
            self.settings.groups_execution = 'requester'; self.settings.save()
            self.assertEqual(ep.eligible_ids(self.item), set())
        with patch('inventory.reference_cleanup.validate_approvals', side_effect=ValidationError('plan changed')):
            self.assertFalse(ep.can_execute(self.second, self.item))

    def test_approver_confirmation_records_actual_actor_and_blocks_double_post(self):
        self.settings.execution_mode = 'approvers'; self.settings.save()
        self.client.force_login(self.second)
        url = reverse('deletion-detail', args=[self.batch.pk])
        payload = {'ticket': 'CHG-test', 'confirm': 'yes', 'execution_item': str(self.item.pk)}
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.confirmed_by, self.second)
        self.assertEqual(self.batch.requested_by, self.requester)
        self.assertEqual(self.batch.confirmed_actor_id, str(self.second.pk))
        stamp = self.batch.confirmed_at
        self.client.post(url, payload)
        self.batch.refresh_from_db(); self.assertEqual(self.batch.confirmed_at, stamp)
        self.second.is_active = False; self.second.save()
        with self.assertRaises(ValidationError): d.execution_authorized(self.batch, self.item)
        self.second.delete(); self.batch.refresh_from_db()
        self.assertIsNone(ep.executor(self.batch))

    def test_mixed_request_leaves_ineligible_objects_pending(self):
        other = deepcopy(self.finding); other.pk = None; other.path = '/infra/domains/default/groups/other'; other.kind = 'empty_group'; other.save()
        pending = DeletionItem.objects.create(batch=self.batch, finding=other, path=other.path, revision=other.revision,
            status='verified', verified_at=self.item.verified_at)
        self.settings.services_execution = 'approvers'; self.settings.groups_execution = 'requester'; self.settings.save()
        self.client.force_login(self.second)
        url = reverse('deletion-detail', args=[self.batch.pk])
        payload = {'ticket': 'CHG-split', 'confirm': 'yes', 'execution_item': str(self.item.pk)}
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        child = DeletionBatch.objects.get(source_request=self.batch)
        self.assertEqual(child.items.get().finding_id, self.finding.pk)
        self.assertEqual(child.confirmed_by, self.second)
        pending.refresh_from_db(); self.assertEqual(pending.status, 'verified')
        self.item.refresh_from_db(); self.assertEqual(self.item.status, 'delegated')
        self.client.post(url, payload)
        self.assertEqual(DeletionBatch.objects.filter(source_request=self.batch).count(), 1)
        self.batch.refresh_from_db(); self.assertEqual(self.batch.status, 'preview_ready')
