from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Finding, InitialPasswordChange, Snapshot, SnapshotComparison, ServiceHeartbeat


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ApplicationImprovementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('review-operator', password='original-test-password', is_staff=True)
        self.client.force_login(self.user)

    def test_reviewer_search_bounded_and_post_selection_validated(self):
        from .forms import FindingReviewForm
        get_user_model().objects.bulk_create([get_user_model()(username=f'person-{i}', first_name='Search', last_name=str(i), is_staff=True) for i in range(200)])
        with CaptureQueriesContext(connection) as queries:
            result = self.client.get(reverse('reviewer-search'), {'q': 'Search'}).json()
        self.assertEqual(len(result['items']), 20)
        self.assertTrue(result['has_more'])
        self.assertTrue(any('LIMIT 21' in q['sql'] for q in queries))
        self.assertLess(len(str(result)), 6000)
        self.assertEqual(FindingReviewForm().fields['owner'].queryset.count(), 0)
        form = FindingReviewForm({'owner':result['items'][0]['id'], 'action':'assign', 'note':'Please review', 'revision':0})
        self.assertTrue(form.is_valid(), form.errors)
        viewer = get_user_model().objects.create_user('non-reviewer')
        form = FindingReviewForm({'owner':viewer.pk, 'action':'assign', 'note':'Please review', 'revision':0})
        self.assertFalse(form.is_valid())
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('reviewer-search')).status_code, 403)

    def test_my_work_excludes_first_approver_from_second_queue(self):
        other = get_user_model().objects.create_user('other-reviewer', is_staff=True)
        for i, owner in enumerate((self.user, other)):
            env = Environment.objects.create(name=f'Env {i}', slug=f'work-{i}', manager=f'https://example-{i}.test')
            now = timezone.now()
            Finding.objects.create(environment=env, name=f'Finding {i}', path=f'/group/{i}', kind='empty_group',
                fingerprint='x', first_seen=now, last_seen=now, evaluated_at=now, owner=owner,
                workflow_state='second_review', approvals={'owner':{'actor_id':str(owner.pk)}})
        response = self.client.get(reverse('my-work'), {'queue':'second'})
        self.assertContains(response, 'Finding 1')
        self.assertNotContains(response, 'Finding 0')
        self.assertEqual(response.context['page'].paginator.count, 1)

    def test_initial_password_must_be_changed_and_cannot_be_reused(self):
        self.user.set_password('NSXSecurityA!'); self.user.save()
        InitialPasswordChange.objects.create(user=self.user)
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse('dashboard')), reverse('password-change'))
        response = self.client.post(reverse('password-change'), {'old_password':'NSXSecurityA!', 'new_password1':'NSXSecurityA!', 'new_password2':'NSXSecurityA!'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(InitialPasswordChange.objects.filter(user=self.user).exists())
        response = self.client.post(reverse('password-change'), {'old_password':'NSXSecurityA!', 'new_password1':'New-unique-password-937!', 'new_password2':'New-unique-password-937!'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(InitialPasswordChange.objects.filter(user=self.user).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('New-unique-password-937!'))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_rotation_migration_preserves_existing_custom_password(self):
        User = get_user_model()
        User.objects.filter(username='admin').delete()
        user = User.objects.create_superuser('admin', password='Custom-existing-password-!')
        migrate = import_module('inventory.migrations.0040_initialpasswordchange_serviceheartbeat_and_more').require_initial_password_rotation
        migrate(apps, SimpleNamespace(connection=connection))
        self.assertFalse(InitialPasswordChange.objects.filter(user=user).exists())
        user.set_password('NSXSecurityA!'); user.save()
        migrate(apps, SimpleNamespace(connection=connection))
        self.assertTrue(InitialPasswordChange.objects.filter(user=user).exists())

    def test_heartbeat_summary_distinguishes_missing_and_stale(self):
        from .heartbeats import summary
        ServiceHeartbeat.objects.create(name='collection', pod='worker-1', seen_at=timezone.now())
        ServiceHeartbeat.objects.create(name='scheduler', pod='scheduler-1', seen_at=timezone.now()-timedelta(minutes=5))
        rows = {r['name']:r for r in summary()}
        self.assertEqual(rows['collection']['status'], 'Reporting')
        self.assertEqual(rows['scheduler']['status'], 'Not reporting')
        self.assertIsNone(rows['recalculation']['seen_at'])

    def test_failed_comparison_retry_requires_post(self):
        env = Environment.objects.create(name='Compare', slug='compare', manager='https://example.test')
        a = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={})
        b = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={})
        pair = SnapshotComparison.objects.create(before=a, after=b, status='failed')
        url = reverse('comparison-retry', args=[pair.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        pair.refresh_from_db(); self.assertEqual(pair.status, 'queued')

    def test_worker_marks_failed_subprocess_comparison(self):
        from .management.commands.audit_worker import Command
        env = Environment.objects.create(name='Compare', slug='compare', manager='https://example.test')
        a = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={})
        b = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={})
        pair = SnapshotComparison.objects.create(before=a, after=b)
        process = MagicMock(); process.poll.return_value = 1
        process.__enter__.return_value = process
        with patch('inventory.management.commands.audit_worker.subprocess.Popen', return_value=process):
            self.assertTrue(Command().run_comparison())
        pair.refresh_from_db(); self.assertEqual(pair.status, 'failed')

        # A comparison timeout must not take down the parallel collection lane.
        pair.status = 'queued'; pair.save(update_fields=['status'])
        process.poll.return_value = None
        with patch('inventory.management.commands.audit_worker.subprocess.Popen', return_value=process), patch('inventory.management.commands.audit_worker.time.monotonic', side_effect=[0, 9999999]):
            self.assertTrue(Command().run_comparison())
        process.kill.assert_called_once()
        pair.refresh_from_db(); self.assertEqual(pair.status, 'failed')
