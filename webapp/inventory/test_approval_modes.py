from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Finding, WorkspacePolicy
from .approval_modes import required
from .finding_workflow import apply_approval_policy


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CategoryApprovalTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('modes-admin', password='test-only')
        self.env = Environment.objects.create(name='Modes', slug='modes', manager='https://example.invalid')
        self.rows = {}
        for kind in ('empty_group', 'unused_service', 'zero_hits', 'empty_policy'):
            self.rows[kind] = Finding.objects.create(environment=self.env, kind=kind, path='/'+kind, name=kind,
                owner=self.admin, workflow_state='second_review', approvals={'owner': {'actor_id':str(self.admin.pk)}},
                first_seen=timezone.now(), last_seen=timezone.now(), evaluated_at=timezone.now())
        self.client.force_login(self.admin)

    def test_preview_does_not_save_then_changes_only_affected_category(self):
        payload = {'required_approvals':2, 'groups_approvals':1, 'services_approvals':0,
                   'rules_approvals':0, 'policies_approvals':0, 'action':'preview'}
        response = self.client.post(reverse('review-approvals'), payload)
        self.assertContains(response, 'Preview changes')
        self.assertFalse(WorkspacePolicy.objects.exists())
        self.rows['empty_group'].refresh_from_db()
        self.assertEqual(self.rows['empty_group'].required_approvals, 2)
        payload['action'] = 'save'
        self.assertEqual(self.client.post(reverse('review-approvals'), payload).status_code, 302)
        for kind, row in self.rows.items():
            row.refresh_from_db()
            self.assertEqual(row.owner, self.admin)
            if kind == 'empty_group':
                self.assertEqual(row.required_approvals, 1)
                self.assertEqual(row.approvals, {})
                self.assertEqual(row.workflow_state, 'owner_review')
                self.assertTrue(row.events.filter(details__action='approval_policy_changed').exists())
            else:
                self.assertEqual(row.required_approvals, 2)
                self.assertTrue(row.approvals)
                self.assertFalse(row.events.exists())

    def test_inheritance_and_terminal_reviews(self):
        settings = WorkspacePolicy(required_approvals=1, groups_approvals=2)
        self.assertEqual(required('unused', settings), 2)
        self.assertEqual(required('empty_group', settings), 2)
        self.assertEqual(required('unused_service', settings), 1)
        self.assertEqual(required('disabled', settings), 1)
        self.assertEqual(required('empty_policy', settings), 1)
        self.rows['unused_service'].workflow_state='rejected'; self.rows['unused_service'].save()
        self.rows['empty_policy'].workflow_state='decommissioned'; self.rows['empty_policy'].save()
        self.assertEqual(apply_approval_policy(settings, self.admin), 1)
        for kind in ('unused_service','empty_policy'):
            row=self.rows[kind]; row.refresh_from_db()
            self.assertEqual(row.required_approvals, 2)
            self.assertTrue(row.approvals)
