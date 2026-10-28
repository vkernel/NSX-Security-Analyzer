from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import finding_recalculation as replay
from .models import (Environment, FindingPolicy, Finding, Snapshot, FindingRecalculation,
                     SnapshotFindingAssessment, FindingEvent, SnapshotFindingEvidenceIndex, AuditJob)
from .findings import synchronize
from .test_foundations import report


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class FindingRecalculationTests(TestCase):
    def setUp(self):
        self.env = Environment.objects.create(name='History', slug='history', manager='https://nsx.example', sync_interval_minutes=1440)
        self.policy = FindingPolicy.objects.create(empty_group_days=30, minimum_observations=2)
        self.now = timezone.now() - timedelta(days=10)
        self.admin = get_user_model().objects.create_superuser('admin-test', password='test-only')
        self.client.force_login(self.admin)

    def snapshot(self, day, state='empty', **kwargs):
        data = kwargs.pop('data', report())
        if state == 'missing': data['objects'] = []
        elif state == 'excluded': data['objects'][0]['audit_exclusions'] = ['excluded']
        else: data['objects'][0]['membership'] = state
        return Snapshot.objects.create(environment=self.env, generated_at=self.now+timedelta(days=day), report=data, **kwargs)

    def recalc(self, kinds=('empty_group',)):
        replay.queue([self.env.pk], kinds)
        job = replay.claim()
        replay.run(job.pk, job.token)
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        return Finding.objects.get(environment=self.env, kind='empty_group')

    def vdi_history(self):
        from .models import TransientEmptinessPolicy
        from .test_transient import config
        rule = TransientEmptinessPolicy.objects.create(name='Citrix VDI Policy', tag_scope='VDI_Gebruiker', mode='monitor', empty_days=3, minimum_observations=3, maximum_gap_hours=24)
        for day in range(9):
            data = report()
            data['objects'][0]['configuration'] = config()
            self.snapshot(day, data=data)
            synchronize(self.env.pk)
        finding = Finding.objects.get(environment=self.env, kind='empty_group')
        self.assertEqual((finding.qualification, finding.required_days), ('eligible', 3))
        data = report(); data['objects'][0]['configuration'] = config()
        data['inventory_warnings'] = [{'endpoint': '/infra/realized-state/virtual-machines', 'detail': 'Changing VM inventory'}]
        self.snapshot(9, data=data)
        synchronize(self.env.pk)
        return rule, finding

    def test_changed_vdi_period_replaces_stale_qualification_despite_coverage_pause(self):
        rule, finding = self.vdi_history()
        # Exercise the GUI save path, including queue creation.
        response = self.client.post(reverse('transient-emptiness'), {'id': rule.pk, 'action': 'save', 'name': rule.name,
            'enabled': 'on', 'environment': '', 'tag_scope': rule.tag_scope, 'reason': 'VDI sessions', 'mode': 'monitor',
            'empty_days': 30, 'minimum_observations': 3, 'maximum_gap_hours': 24})
        self.assertEqual(response.status_code, 302)
        job = replay.claim(); replay.run(job.pk, job.token)
        finding.refresh_from_db(); job.refresh_from_db()
        self.assertEqual((finding.required_days, finding.qualification), (30, 'insufficient'))
        self.assertIsNone(finding.qualified_at)
        self.assertIn('Criteria changed; verification paused', finding.qualification_reason)
        self.assertEqual(job.paused, 1)
        response = self.client.get(reverse('findings', args=[self.env.pk]), {'owner': 'none', 'kind': 'empty_group'})
        self.assertNotContains(response, 'data-assign-eligible="true"')
        self.assertContains(response, '1 paused for incomplete evidence')
        # Repeating replay must not restore stale eligibility.
        finding = self.recalc()
        self.assertEqual((finding.required_days, finding.qualification), (30, 'insufficient'))

    def test_changed_policy_invalidates_approvals_but_keeps_owner_and_history(self):
        rule, finding = self.vdi_history()
        finding.workflow_state='ready'; finding.owner=self.admin
        finding.approvals={'owner': {'actor_id': str(self.admin.pk)}}; finding.save()
        rule.empty_days=30; rule.save()
        finding=self.recalc()
        self.assertEqual(finding.workflow_state, 'owner_review')
        self.assertEqual(finding.owner_id, self.admin.pk)
        self.assertEqual(finding.approvals, {})
        self.assertTrue(finding.events.filter(details__action='approval_invalidated').exists())

    def test_unchanged_policy_preserves_paused_decisions(self):
        rule, finding = self.vdi_history()
        finding.workflow_state='ready'; finding.owner=self.admin
        finding.approvals={'owner': {'actor_id': str(self.admin.pk)}}; finding.save()
        finding=self.recalc()
        self.assertEqual(finding.required_days, 3)
        self.assertEqual(finding.workflow_state, 'ready')
        self.assertTrue(finding.approvals)

    def test_replay_does_not_bridge_unverified_observations(self):
        self.policy.empty_group_days=3; self.policy.maximum_gap_hours=96; self.policy.save()
        self.snapshot(0); self.snapshot(1)
        data=report(); data['inventory_warnings']=[{'endpoint':'/infra/realized-state/virtual-machines'}]
        self.snapshot(2,data=data); self.snapshot(3)
        finding=self.recalc()
        self.assertEqual(finding.qualification,'observing')
        self.assertEqual(finding.observation_count,1)
        self.assertEqual(finding.observation_days,0)

    def test_publication_skips_unchanged_evidence_but_advances_observation(self):
        self.snapshot(0)
        synchronize(self.env.pk)
        before = Finding.objects.get(environment=self.env, kind='empty_group')
        latest = self.snapshot(1)
        with patch.object(Finding.objects, 'bulk_update', wraps=Finding.objects.bulk_update) as update:
            synchronize(self.env.pk)
        fields = [call.args[1] for call in update.call_args_list]
        self.assertTrue(fields)
        self.assertFalse(any('evidence' in selected for selected in fields))
        current = Finding.objects.get(pk=before.pk)
        self.assertEqual(current.evidence, before.evidence)
        self.assertEqual(current.observation_count, before.observation_count + 1)
        self.assertEqual(current.snapshot_id, latest.pk)
        data = report()
        data['objects'][0]['configuration'] = {'changed': True}
        self.snapshot(2, data=data)
        synchronize(self.env.pk)
        current.refresh_from_db()
        self.assertEqual(current.evidence['configuration'], {'changed': True})

    def test_existing_history_qualifies_without_collection_and_preserves_reviews(self):
        for day in range(4):
            self.snapshot(day)
            synchronize(self.env.pk)
        f = Finding.objects.get(environment=self.env, kind='empty_group')
        f.status = 'acknowledged'; f.owner = self.admin; f.save()
        note = FindingEvent.objects.create(finding=f, message='Retain this group')
        assessments = list(SnapshotFindingAssessment.objects.order_by('pk').values_list('assessment', flat=True))
        self.policy.empty_group_days = 2; self.policy.save()
        f = self.recalc()
        self.assertEqual((f.qualification, f.observation_days, f.observation_count), ('eligible', 3, 4))
        self.assertEqual(f.observation_started, self.now)
        self.assertEqual((f.status, f.owner_id), ('acknowledged', self.admin.pk))
        self.assertTrue(FindingEvent.objects.filter(pk=note.pk).exists())
        self.assertEqual(assessments, list(SnapshotFindingAssessment.objects.order_by('pk').values_list('assessment', flat=True)))
        # Subsequent recalculation reuses the compact index.
        with patch('inventory.finding_recalculation.source_rows', side_effect=AssertionError('cache not reused')):
            self.recalc()
        self.assertEqual(Snapshot.objects.count(), 4)

    def test_condition_break_starts_at_current_sequence(self):
        self.policy.empty_group_days = 1; self.policy.save()
        self.snapshot(0); self.snapshot(1, 'nonempty'); self.snapshot(2); self.snapshot(3)
        f = self.recalc()
        self.assertEqual(f.observation_started, self.now+timedelta(days=2))
        self.assertEqual((f.qualification, f.observation_days, f.observation_count), ('eligible', 1, 2))

    def test_missing_excluded_unknown_and_gap_break_history(self):
        self.policy.empty_group_days = 1; self.policy.save()
        self.snapshot(0); self.snapshot(1, 'unknown'); self.snapshot(2)
        self.assertEqual(self.recalc().qualification, 'observing')
        self.snapshot(3, 'excluded'); self.snapshot(4)
        self.assertEqual(self.recalc().observation_count, 1)
        self.snapshot(5, 'missing'); self.snapshot(6)
        self.assertEqual(self.recalc().observation_count, 1)
        self.snapshot(10)
        self.assertEqual(self.recalc().observation_count, 1)

    def test_zero_latest_and_configuration_change(self):
        self.snapshot(0); self.snapshot(1)
        changed = report(); changed['objects'][0]['unique_id'] = 'new-object'
        self.snapshot(2, data=changed)
        self.policy.empty_group_days = 0; self.policy.save()
        f = self.recalc()
        self.assertEqual((f.qualification, f.observation_count), ('eligible', 1))
        self.snapshot(3, 'unknown')
        self.assertEqual(self.recalc().qualification, 'insufficient')

    def test_failed_testing_imported_snapshots_do_not_establish_period(self):
        self.policy.empty_group_days = 1; self.policy.save()
        self.snapshot(0)
        failed = AuditJob.objects.create(environment=self.env, status='failed')
        self.snapshot(1, 'nonempty', job=failed)
        self.snapshot(1, 'nonempty', testing=True)
        self.snapshot(1, 'nonempty', imported=True)
        self.snapshot(2)
        f = self.recalc()
        self.assertEqual((f.qualification, f.observation_count), ('eligible', 2))
        self.assertEqual(SnapshotFindingEvidenceIndex.objects.count(), 2)

    def test_new_request_supersedes_running_and_new_snapshot_requeues(self):
        self.snapshot(0)
        replay.queue([self.env.pk], ['empty_group'])
        first = replay.claim()
        replay.queue([self.env.pk], ['unused'])
        self.assertFalse(FindingRecalculation.objects.filter(pk=first.pk, token=first.token).exists())
        job = replay.claim()
        original = replay.index
        def index_and_collect(pk):
            original(pk)
            self.snapshot(1)
        with patch('inventory.finding_recalculation.index', side_effect=index_and_collect): replay.run(job.pk, job.token)
        job.refresh_from_db(); self.assertEqual(job.status, 'queued')
        self.assertEqual(Finding.objects.count(), 0)

    def test_save_queues_only_affected_types_and_respects_overrides(self):
        other = Environment.objects.create(name='Other', slug='other', manager='https://other.example')
        FindingPolicy.objects.create(scope=str(other.pk), environment=other)
        fields = ('zero_hits_days', 'empty_group_days', 'unused_days', 'unused_service_days', 'empty_policy_days', 'disabled_days', 'minimum_observations', 'maximum_gap_hours')
        data = {field: getattr(self.policy, field) for field in fields}
        data['empty_group_days'] = 1
        self.assertEqual(self.client.post(reverse('finding-policy'), data).status_code, 302)
        job = FindingRecalculation.objects.get(environment=self.env)
        self.assertEqual(job.kinds, ['empty_group'])
        self.assertFalse(FindingRecalculation.objects.filter(environment=other).exists())
        self.assertContains(self.client.get(reverse('finding-policy')), 'waiting for worker')
        self.assertContains(self.client.get(reverse('findings', args=[self.env.pk])), 'waiting for worker')

    def test_zero_hit_requires_fresh_counter_each_observation(self):
        self.policy.zero_hits_days = 1; self.policy.save()
        for day in range(3):
            data = report()
            data['dfw']['rules'] = [{'path': '/rule/r', 'name': 'Rule', 'hit_status': 'zero_hits', 'hit_count': 0,
                                    'statistics_checked_at': self.now.isoformat(), 'disabled': False}]
            self.snapshot(day, data=data)
        self.recalc(('zero_hits', 'empty_group'))
        self.assertEqual(Finding.objects.get(kind='zero_hits').qualification, 'insufficient')

    def test_worker_dispatches_recalculation(self):
        import io
        from django.core.management import call_command
        replay.queue([self.env.pk], ['empty_group'])
        with patch('inventory.management.commands.audit_worker.close_old_connections'), patch('inventory.management.commands.audit_worker.Command.run_recalculation') as dispatch:
            call_command('audit_worker', once=True, stdout=io.StringIO())
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[0].status, 'running')

    def test_replay_restores_absent_state_and_evidence_without_losing_review(self):
        self.policy.empty_group_days = 2; self.policy.save()
        for day in range(4): self.snapshot(day)
        synchronize(self.env.pk)
        f = Finding.objects.get(kind='empty_group'); f.present=False; f.status='acknowledged'; f.owner=self.admin; f.evidence={}; f.save()
        f = self.recalc()
        self.assertTrue(f.present)
        self.assertEqual(f.qualification, 'eligible')
        self.assertEqual(f.evidence['membership'], 'empty')
        self.assertEqual((f.status, f.owner_id), ('acknowledged', self.admin.pk))
        response = self.client.get(reverse('findings', args=[self.env.pk]))
        self.assertEqual(len(response.context['page']), 1)

    def test_replay_condition_fingerprint_and_old_cache_upgrade(self):
        self.policy.empty_group_days=3; self.policy.save()
        for day in range(4):
            data=report(); data['objects'][0]['referenced_by']=['/rule/'+str(day)]
            snapshot=self.snapshot(day, data=data)
            SnapshotFindingEvidenceIndex.objects.create(snapshot=snapshot, version=1)
        f=self.recalc()
        self.assertEqual((f.qualification, f.observation_days), ('eligible', 3))
        self.assertFalse(SnapshotFindingEvidenceIndex.objects.filter(version=1).exists())
        self.assertEqual(f.evidence['membership'], 'empty')
        self.snapshot(4); synchronize(self.env.pk)
        f.refresh_from_db(); self.assertEqual(f.observation_count, 5)

    def test_help_uses_current_guidance_without_index_refresh(self):
        snap=self.snapshot(0)
        response=self.client.get(reverse('snapshot-data', args=[snap.pk]), {'op':'section','panel':'feature-guide'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Why some findings are not listed', response.json()['html'])
        self.assertIn('minimum of 24 hours', response.json()['html'])
        for text in ('Return to unassigned', 'Compare selected', '1237/1238',
                     'three attempts in total', 'Start diagnostic collection'):
            self.assertIn(text, response.json()['html'])
