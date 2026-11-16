"""One collection page: phases, saved pacing, coverage and history; no secrets, no NSX."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AuditJob, Environment, Snapshot, SnapshotCoverage, SnapshotCoverageIssue


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CollectionDetailTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.staff = users.objects.create_user('collection-staff', is_staff=True)
        self.viewer = users.objects.create_user('collection-viewer')
        self.env = Environment.objects.create(name='Collected', slug='collected', manager='https://collected.invalid')
        now = timezone.now()
        config = {'workers': 4, 'max_concurrent_requests': 8, 'password_ciphertext': 'SECRET-CIPHERTEXT'}
        self.failed = AuditJob.objects.create(environment=self.env, status='failed', config=config, error='Synthetic timeout',
                                              started_at=now - timedelta(hours=3), finished_at=now - timedelta(hours=3) + timedelta(minutes=7), progress_completed=3)
        self.done = AuditJob.objects.create(environment=self.env, status='succeeded', config=config, scheduled=True,
                                            started_at=now - timedelta(hours=1), finished_at=now - timedelta(minutes=10), progress_completed=7)
        self.snapshot = Snapshot.objects.create(environment=self.env, job=self.done, generated_at=now, needs_review=True, summary={},
            report={'performance': {'http': {'requests': 413, 'retries': 2}, 'request_pacing': {'throttled_attempts': 5, 'average_requests_per_second': 7.5},
                                    'phases_seconds': {'membership': 12.5, 'dfw': 3.0}}})
        coverage = SnapshotCoverage.objects.create(snapshot=self.snapshot, issue_count=1)
        SnapshotCoverageIssue.objects.create(coverage=coverage, ordinal=0, area='Group membership', name='g', detail='unknown')
        self.client.force_login(self.staff)

    def test_finished_collection_shows_phases_pacing_coverage_and_no_secrets(self):
        response = self.client.get(reverse('collection-detail', args=[self.done.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({phase['state'] for phase in response.context['phases']}, {'done'})
        self.assertEqual((response.context['pacing']['requests'], response.context['pacing']['throttled']), (413, 5))
        self.assertContains(response, 'Completed · coverage needs review')
        self.assertContains(response, 'data-poll="false"')
        self.assertContains(response, reverse('snapshot', args=[self.snapshot.pk]))
        rows = {row['area']: row['state'] for row in response.context['coverage']['rows']}
        self.assertEqual(rows['Group membership'], 'Needs review')
        self.assertNotContains(response, 'SECRET-CIPHERTEXT')
        self.assertNotContains(response, 'password')
        self.assertEqual([row.pk for row in response.context['history']], [self.done.pk, self.failed.pk])
        self.assertEqual(response.context['history'][1].coverage, 'Previous snapshot kept')

    def test_failed_and_running_collections_keep_unknown_states(self):
        response = self.client.get(reverse('collection-detail', args=[self.failed.pk]))
        states = [phase['state'] for phase in response.context['phases']]
        self.assertEqual(states[:5], ['done', 'done', 'done', 'stopped', 'skipped'])
        self.assertContains(response, 'Earlier snapshots remain available')
        self.assertIsNone(response.context['coverage'])
        running = AuditJob.objects.create(environment=Environment.objects.create(name='Busy', slug='busy', manager='https://busy.invalid'),
                                          status='running', progress_completed=2, progress_stage='Completing firewall rules and counters')
        response = self.client.get(reverse('collection-detail', args=[running.pk]))
        self.assertEqual([phase['state'] for phase in response.context['phases']][1:4], ['done', 'current', 'waiting'])
        self.assertContains(response, 'data-poll="true"')
        self.assertContains(response, 'unknown, not complete')
        self.assertContains(response, reverse('stop-collection', args=[running.pk]))
        # Status polling returns only the live region.
        fragment = self.client.get(reverse('collection-detail', args=[running.pk]), HTTP_X_REQUESTED_WITH='task-status')
        self.assertContains(fragment, 'id="collection-status"')
        self.assertNotContains(fragment, 'collection-history-title')

    def test_viewers_see_no_staff_controls_and_login_is_required(self):
        running = AuditJob.objects.create(environment=Environment.objects.create(name='Busy', slug='busy', manager='https://busy.invalid'), status='queued')
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('collection-detail', args=[running.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse('stop-collection', args=[running.pk]))
        self.assertNotContains(response, reverse('collection-diagnostics', args=[running.pk]))
        self.client.logout()
        self.assertEqual(self.client.get(reverse('collection-detail', args=[running.pk])).status_code, 302)

    def test_collection_lists_link_to_the_collection_page(self):
        response = self.client.get(reverse('collection-history', args=[self.env.pk]))
        self.assertContains(response, reverse('collection-detail', args=[self.done.pk]))

    def test_completed_collection_shows_result_cards_and_next_steps(self):
        from .models import Finding
        self.snapshot.summary = {'vms': 10, 'groups': 5, 'rules': 7, 'policies': 2}
        self.snapshot.save(update_fields=['summary'])
        now = timezone.now()
        Finding.objects.create(environment=self.env, snapshot=self.snapshot, kind='empty_group', path='/g', name='g', fingerprint='g',
                               first_seen=now, last_seen=now, evaluated_at=now)
        self.env.sync_interval_minutes = 60; self.env.next_sync_at = now + timedelta(hours=1)
        self.env.save(update_fields=['sync_interval_minutes', 'next_sync_at'])
        response = self.client.get(reverse('collection-detail', args=[self.done.pk]))
        result = response.context['result']
        self.assertEqual(result['objects'], 24)
        self.assertIsNone(result['total_changes'])            # no earlier non-testing snapshot
        texts = [step['text'] for step in result['steps']]
        self.assertIn('1 finding evaluated from this snapshot', texts)
        self.assertTrue(any('wait for complete coverage' in text for text in texts))
        self.assertEqual(texts[-1], 'Next scheduled collection')
        self.assertContains(response, 'Collection result')
        self.assertContains(response, 'No earlier snapshot')
        self.assertIsNone(self.client.get(reverse('collection-detail', args=[self.failed.pk])).context['result'])
