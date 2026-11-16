"""Publish synthetic demonstration data through the application's own pipeline.

Snapshots are produced by the real collector (``collector.audit``) reading a
synthetic manager in process, then saved, indexed and turned into findings the way
``services.execute_job`` does it. Collection times are simulated by shifting the
report's timestamps before publication; nothing contacts a network.
"""
from datetime import datetime, timedelta
import logging
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .. import collector, services
from ..audit_events import record
from ..credentials import decrypt_password, encrypt_password
from ..diagnostics import log_failure
from ..models import (AuditEvent, AuditJob, DeletionBatch, Environment, Finding, FindingEvent, FindingPolicy,
                      NamingPolicy, SegmentationSettings, Snapshot)
from ..roles import apply_role
from .scenario import PROFILES, build
from .transport import DemoNSXClient, SyntheticManager, no_network

LOG = logging.getLogger('inventory.demo_data')
DEMO_HOST_SUFFIX = '.example.invalid'
NAMING_PREFIX = 'Demo '
USERS = (
    ('demo-admin', 'admin', 'Demo', 'Administrator'),
    ('demo-approver', 'admin', 'Demo', 'Second approver'),
    ('demo-operator-a', 'operator', 'Demo', 'Operator A'),
    ('demo-operator-b', 'operator', 'Demo', 'Operator B'),
    ('demo-viewer', 'viewer', 'Demo', 'Viewer'),
)
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$')
# Shorter observation periods than the defaults so roughly five weeks of synthetic
# collections produce eligible findings. Stored as environment overrides.
DEMO_FINDING_POLICY = dict(zero_hits_days=21, empty_group_days=14, unused_days=21, unused_service_days=21,
                           empty_policy_days=14, disabled_days=14, minimum_observations=3, maximum_gap_hours=240)


def is_demo_environment(environment):
    host = urlsplit(environment.manager).hostname or ''
    return environment.slug.startswith('demo-') and host.endswith(DEMO_HOST_SUFFIX)


def demo_environments():
    return [env for env in Environment.objects.all() if is_demo_environment(env)]


def foreign_data():
    """Environments or snapshots that this loader did not create."""
    demo = {env.pk for env in demo_environments()}
    return (Environment.objects.exclude(pk__in=demo).exists()
            or Snapshot.objects.exclude(environment_id__in=demo).exists())


def check_credentials_path(path):
    path = Path(path).expanduser().absolute()
    base = Path(settings.BASE_DIR).resolve()
    protected = [base]
    if (base.parent / '.git').exists():
        protected.append(base.parent)
    resolved = path.parent.resolve() / path.name
    if any(resolved == root or root in resolved.parents for root in protected):
        raise ValueError('Write the credentials file outside the application source tree.')
    if not resolved.parent.is_dir():
        raise ValueError('The credentials file directory does not exist.')
    return resolved


def write_credentials(path, accounts, note):
    lines = ['# NSX Security Analyzer demonstration accounts (synthetic data only).',
             '# Generated ' + timezone.now().strftime('%Y-%m-%d %H:%M UTC') + '. Keep this file private.',
             '# ' + note, '', 'username\trole\tpassword']
    lines += ['\t'.join(row) for row in accounts]
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, ('\n'.join(lines) + '\n').encode())
    finally:
        os.close(descriptor)


@transaction.atomic
def reset():
    """Remove only data created by this loader. Audit events are immutable and stay."""
    removed = []
    for environment in demo_environments():
        DeletionBatch.objects.filter(environment=environment).delete()
        environment.snapshots.all().delete()
        environment.jobs.all().delete()
        removed.append(environment.name)
        environment.delete()
    NamingPolicy.objects.filter(name__startswith=NAMING_PREFIX, environment__isnull=True).delete()
    get_user_model().objects.filter(username__in=[row[0] for row in USERS]).delete()
    return removed


def shift_timestamps(value, delta, window):
    """Move collection-time ISO timestamps to the simulated collection time."""
    if isinstance(value, dict):
        return {key: shift_timestamps(item, delta, window) for key, item in value.items()}
    if isinstance(value, list):
        return [shift_timestamps(item, delta, window) for item in value]
    if isinstance(value, str) and ISO.match(value):
        try:
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return value
        if stamp.tzinfo is not None and window[0] <= stamp <= window[1]:
            return (stamp + delta).isoformat()
    return value


class Loader:
    def __init__(self, stdout=None, anchor=None):
        self.stdout = stdout
        self.now = anchor or timezone.now()
        self.users = {}
        self.accounts = []
        self.environments = {}

    def say(self, message):
        if self.stdout is not None:
            self.stdout.write(message)
            self.stdout.flush()

    # Accounts ---------------------------------------------------------------------
    def create_users(self):
        model = get_user_model()
        for username, role, first, last in USERS:
            password = secrets.token_urlsafe(18)
            user = model(username=username, first_name=first, last_name=last,
                         email=username + '@example.invalid', is_active=True)
            apply_role(user, role)
            user.set_password(password)
            user.save()
            record('user.created', 'User', user.pk, details={'source': 'demonstration_data', 'role': role})
            self.users[username] = user
            self.accounts.append((username, role, password))
        self.say('Created %d demonstration accounts.' % len(USERS))

    # Environments and collections ------------------------------------------------
    def create_environment(self, profile):
        environment = Environment(slug=profile.slug, name=profile.name, manager=profile.manager,
                                  username='svc-demo-readonly', enabled=False, sync_interval_minutes=0,
                                  workers=4, max_concurrent_requests=8, timeout=30, retries=2)
        environment.full_clean(exclude=['password_ciphertext'])
        # A random throwaway credential: the synthetic manager accepts anything.
        environment.password_ciphertext = encrypt_password(secrets.token_urlsafe(24))
        environment.save()
        FindingPolicy.objects.create(scope='demo-' + str(environment.pk), environment=environment, **DEMO_FINDING_POLICY)
        SegmentationSettings.objects.create(environment=environment, grouping_scope='app')
        record('environment.created', 'Environment', environment.pk, actor=self.users['demo-admin'].pk,
               details={'source': 'demonstration_data'})
        self.environments[profile.key] = environment
        return environment

    def collect(self, environment, inventory, when, requested_by=None, mode='normal'):
        """One collection through the real collector; returns the snapshot or None."""
        real_start = timezone.now()
        duration = timedelta(seconds=240 + len(inventory.vms) + len(inventory.groups))
        job = AuditJob.objects.create(environment=environment, requested_by=requested_by,
                                      scheduled=requested_by is None, config=environment.collection_config(),
                                      status='running', started_at=real_start, progress_stage='Starting collection')
        config = job.config
        client = DemoNSXClient(config['manager'], config['username'], decrypt_password(config['password_ciphertext']),
                               SyntheticManager(inventory, mode), timeout=config['timeout'],
                               retries=config['retries'], insecure=config['insecure'])
        snapshot = None
        try:
            with no_network():
                snapshot = self._collect(job, client, environment, when)
            outcome = 'succeeded'
        except Exception as exc:  # Mirrors execute_job's failure recording.
            failure = log_failure(job.pk, exc)
            detail = failure['chain'][0]['message'] if failure.get('chain') else 'Collection failed.'
            services.fail_job(job.pk, detail, {'timeline': [], 'error': failure})
            outcome = 'failed'
        finally:
            from ..nsx_access import save_collection_access
            save_collection_access(client, job)
            client.close()
        if snapshot is not None:
            Snapshot.objects.filter(pk=snapshot.pk).update(created_at=when + timedelta(seconds=20),
                                                          browsing_refreshed_at=when + timedelta(seconds=50))
        finished = when + timedelta(seconds=25 if outcome == 'succeeded' else 0)
        AuditJob.objects.filter(pk=job.pk).update(created_at=when - duration - timedelta(seconds=30),
                                                  started_at=when - duration, finished_at=finished)
        FindingEvent.objects.filter(finding__environment=environment, created_at__gte=real_start).update(created_at=finished)
        findings = [str(pk) for pk in Finding.objects.filter(environment=environment).values_list('pk', flat=True)]
        AuditEvent.objects.filter(created_at__gte=real_start).filter(
            Q(target_type='AuditJob', target_id=str(job.pk)) | Q(target_type='Finding', target_id__in=findings)
        ).update(created_at=finished)
        return snapshot

    def _collect(self, job, client, environment, when):
        from ..previous_evidence import load
        previous = load(environment)
        host = urlsplit(client.base_url).netloc
        client.statistics_backoff = collector.statistics_backoff(previous, host)
        client.vm_fast_disabled = bool((previous or {}).get('vm_fast_disabled', False))
        client.membership_hints = {}
        for row in (previous or {}).get('objects', []):
            if row.get('kind') == 'group' and row.get('membership') == 'nonempty':
                for note in row.get('notes', []):
                    prefix = 'Resolved members found: '
                    if note.startswith(prefix) and note[len(prefix):] in collector.MEMBERSHIP_ENDPOINTS:
                        client.membership_hints[row['path']] = note[len(prefix):]
        client.request_deadline = time.monotonic() + 900
        window_start = timezone.now() - timedelta(seconds=5)
        report = collector.audit(client, workers=4, testing=False,
                                 progress=lambda completed, stage: services.update_progress(job.pk, completed, stage))
        window = (window_start, timezone.now() + timedelta(seconds=5))
        if previous and previous.get('manager') == host:
            before = {r['path'] for r in previous.get('objects', []) if r.get('kind') == 'group'}
            after = {r['path'] for r in report.get('objects', []) if r.get('kind') == 'group'}
            report.setdefault('performance', {}).setdefault('collection_scope', {})['comparison'] = {
                'previous_at': previous.get('generated_at'), 'previous_groups': len(before),
                'current_groups': len(after), 'added': len(after - before), 'removed': len(before - after),
                'added_samples': sorted(after - before)[:10], 'removed_samples': sorted(before - after)[:10]}
        from ..version import VERSION, BUILD
        report.setdefault('performance', {})['collector_build'] = {
            'version': VERSION, 'revision': BUILD['revision'], 'built_at': BUILD['built_at'],
            'configured_maximum': job.config.get('max_concurrent_requests', 8), 'diagnostics_schema': 2,
            'synthetic_demonstration': True}
        report['manager'] = host
        generated = datetime.fromisoformat(report['generated_at'])
        report = shift_timestamps(report, when - generated, window)
        collector.retain_hit_history(report, previous)
        services.update_progress(job.pk, 5, 'Preparing report and hit history')
        snapshot = services.prepare_snapshot(environment, report)
        services.update_progress(job.pk, 6, 'Saving snapshot')
        with transaction.atomic():
            current = AuditJob.objects.select_for_update().get(pk=job.pk)
            snapshot.job = current
            snapshot.browsing_status = 'indexing'
            snapshot.save()
            from ..findings import synchronize
            synchronize(environment.pk, collected_snapshot=snapshot, job_id=job.pk)
            current.status = 'succeeded'
            current.finished_at = timezone.now()
            current.progress_completed = 7
            current.progress_stage = ('Completed with coverage warnings' if report.get('inventory_warnings')
                                      else 'Completed')
            current.save(update_fields=['status', 'finished_at', 'progress_completed', 'progress_stage'])
        record('collection.completed', 'AuditJob', job.pk, details={'snapshot_id': str(snapshot.pk),
               'elapsed_seconds': 0, 'synthetic_demonstration': True}, best_effort=True)
        services.index_published_snapshot(job.pk, snapshot)
        return snapshot

    def run_profile(self, profile):
        environment = self.create_environment(profile)
        count = len(profile.snapshot_days)
        events = [(days, 'collect', index) for index, days in enumerate(profile.snapshot_days)]
        events += [(days, mode, None) for days, mode in profile.failures]
        events += [(days, 'cancel', None) for days in profile.cancelled]
        snapshots = []
        operator = self.users['demo-operator-a']
        for days, action, index in sorted(events, key=lambda item: -item[0]):
            when = self.now - timedelta(days=days)
            if action == 'cancel':
                self.cancelled(environment, when, operator)
                continue
            position = index if index is not None else max(0, sum(1 for d in profile.snapshot_days if d > days) - 1)
            inventory = build(profile, position, count, self.now)
            started = time.monotonic()
            requested_by = operator if (index or 0) % 3 == 1 or action != 'collect' else None
            snapshot = self.collect(environment, inventory, when, requested_by,
                                    mode='normal' if action == 'collect' else action)
            if snapshot is not None:
                snapshots.append(snapshot)
            self.say('  %s %s collection %s (%.1fs)' % (
                environment.name, when.strftime('%Y-%m-%d %H:%M'),
                'published' + (' with coverage warnings' if inventory.partial else '') if snapshot else 'failed (' + action + ')',
                time.monotonic() - started))
        return environment, snapshots

    def cancelled(self, environment, when, user):
        job = AuditJob.objects.create(environment=environment, requested_by=user, config=environment.collection_config(),
                                      status='running', started_at=timezone.now(), progress_completed=2,
                                      progress_stage='Scanning references, firewall counters and VM associations')
        services.finish_stopped_job(job.pk)
        AuditJob.objects.filter(pk=job.pk).update(created_at=when - timedelta(seconds=40),
                                                  started_at=when - timedelta(seconds=30), finished_at=when)
        AuditEvent.objects.filter(target_id=str(job.pk)).update(created_at=when)

    # Browsing extras --------------------------------------------------------------
    def prepare_browsing(self, environment, snapshots):
        from ..analysis_index import prepare
        from ..comparison_cache import comparison_rows
        if len(snapshots) >= 2:
            list(comparison_rows(snapshots[-2], snapshots[-1]))
            list(comparison_rows(snapshots[0], snapshots[-1]))
        prepare(Snapshot.objects.get(pk=snapshots[-1].pk))

    def naming_policies(self, scoped_environment=None):
        from ..naming_views import snapshot_version
        base = dict(literal='', required=True, mapping={})
        tag_policy = {'target': 'group', 'group_type': 'dynamic', 'separator': '-', 'max_length': 63,
                      'match_mode': 'exact', 'match_field': '', 'match_value': '', 'components': [
                          dict(base, field='Environment', source='tag', scope='env', case='upper', format='alphanumeric',
                               max_length=8, mapping={'production': 'PRD', 'dr': 'DR', 'lab': 'LAB'}),
                          dict(base, field='Application', source='tag', scope='app', case='upper',
                               format='alphanumeric', max_length=16),
                          dict(base, field='Role', source='tag', scope='tier', case='upper', format='alphanumeric',
                               max_length=8, required=False)]}
        host_policy = {'target': 'group', 'group_type': 'static', 'separator': '-', 'max_length': 128,
                       'match_mode': 'single_host_ip', 'match_field': '', 'match_value': '', 'components': [
                           dict(base, field='ObjectType', source='fixed', literal='HST', scope='', case='keep',
                                format='extended', max_length=8),
                           dict(base, field='Address', source='single_host_ip', scope='', case='keep',
                                format='ip_address', max_length=45)]}
        admin = self.users['demo-admin']
        environments = [scoped_environment] if scoped_environment else [None]
        for environment in environments:
            for name, config in (('Demo application group names', tag_policy), ('Demo host address groups', host_policy)):
                policy = NamingPolicy.objects.create(name=name, environment=environment, draft=config,
                                                     configuration=config, enabled=True)
                snapshot_version(policy, admin, 'activated')

    # Review workflow ----------------------------------------------------------------
    def review_workflow(self, environment):
        from ..finding_workflow import decide
        from ..related_decommission import related_findings
        users = self.users
        eligible = Finding.objects.filter(environment=environment, present=True, qualification='eligible')

        def pick(kind, name_prefix=''):
            candidates = eligible.filter(kind=kind, workflow_state='unassigned', owner__isnull=True)
            if name_prefix:
                candidates = candidates.filter(name__startswith=name_prefix)
            return candidates.order_by('name').first()

        def act(finding, actor, action, reason, **options):
            if finding is None:
                return None
            with no_network():
                decide(Finding.objects.get(pk=finding.pk), actor, action, reason, **options)
            return Finding.objects.get(pk=finding.pk)

        environment_code = next(p.code for p in PROFILES if p.slug == environment.slug)
        done = {}
        # Owner review for two owners.
        for kind, prefix, owner in (('zero_hits', '', 'demo-operator-a'), ('unused', 'DECOM-PILOT', 'demo-operator-a'),
                                    ('empty_group', environment_code + '-FUTURE', 'demo-admin'),
                                    ('unused', 'SANDBOX-TRIAL', 'demo-admin'),
                                    ('unused_service', 'SVC-TEST', 'demo-operator-b')):
            finding = pick(kind, prefix)
            done.setdefault('owner_review', []).append(
                act(finding, users['demo-admin'], 'assign', 'Owner assigned for demonstration review.', owner=users[owner]))
        # Awaiting second approval.
        for kind, prefix, owner in (('disabled', '', 'demo-operator-a'), ('unused', 'OLD-MIGRATION', 'demo-admin')):
            finding = act(pick(kind, prefix), users['demo-admin'], 'assign', 'Owner assigned.', owner=users[owner])
            done.setdefault('second_review', []).append(
                act(finding, users[owner], 'approve', 'Confirmed with the application team; no remaining dependency.'))
        # Ready for decommissioning (both approvals recorded).
        finding = act(pick('unused', 'TMP-PROJECT'), users['demo-admin'], 'assign', 'Owner assigned.',
                      owner=users['demo-operator-b'])
        finding = act(finding, users['demo-operator-b'], 'approve', 'Project closed; group no longer required.')
        done['ready'] = [act(finding, users['demo-approver'], 'approve', 'Second review completed.')]
        # Completed (decommissioning recorded with a change ticket).
        finding = act(pick('unused_service', 'SVC-LEGACY'), users['demo-admin'], 'assign', 'Owner assigned.',
                      owner=users['demo-operator-b'])
        finding = act(finding, users['demo-operator-b'], 'approve', 'Legacy transfer service retired.')
        finding = act(finding, users['demo-approver'], 'approve', 'Second review completed.')
        if finding is not None and not related_findings(finding).exists():
            finding = act(finding, users['demo-operator-b'], 'complete', 'Removed during the monthly clean-up window.',
                          ticket='CHG-DEMO-0042')
        done['decommissioned'] = [finding]
        # Rejected by the owner.
        finding = act(pick('unused', 'IPS-OLD-PARTNER') or pick('unused', 'IPS-'), users['demo-admin'], 'assign',
                      'Owner assigned.', owner=users['demo-operator-a'])
        done['rejected'] = [act(finding, users['demo-operator-a'], 'reject',
                                'Still required for a partner connection that is being re-established.')]
        self.cancelled_deletion_preview(environment, done['ready'][0])
        return {state: [f.name for f in rows if f is not None] for state, rows in done.items()}

    def cancelled_deletion_preview(self, environment, finding):
        """A preview requested and cancelled before any live check: no NSX request."""
        from ..models import DeletionItem
        if finding is None:
            return
        admin = self.users['demo-admin']
        when = self.now - timedelta(minutes=50)
        batch = DeletionBatch.objects.create(environment=environment, manager=environment.manager, requested_by=admin,
                                             status='cancelled', finished_at=when + timedelta(minutes=2))
        DeletionItem.objects.create(batch=batch, finding=finding, revision=finding.revision, path=finding.path,
                                    name=finding.name, status='cancelled', finished_at=when + timedelta(minutes=2),
                                    message='Deletion request cancelled. Cancelled before the preview worker started; '
                                            'no request was sent to NSX.')
        record('nsx.deletion_preview_requested', 'DeletionBatch', batch.pk, actor=admin.pk,
               details={'environment_id': environment.pk, 'count': 1})
        record('nsx.deletion_cancel_requested', 'DeletionBatch', batch.pk, actor=admin.pk,
               details={'environment_id': environment.pk})
        DeletionBatch.objects.filter(pk=batch.pk).update(created_at=when)

    # Orchestration -----------------------------------------------------------------
    def run(self, foreign=False):
        started = time.monotonic()
        self.create_users()
        results = {}
        for profile in PROFILES:
            self.say('Collecting synthetic inventory for %s...' % profile.name)
            environment, snapshots = self.run_profile(profile)
            self.prepare_browsing(environment, snapshots)
            results[profile.key] = (environment, snapshots)
        if foreign:
            for environment, _ in results.values():
                self.naming_policies(environment)  # Never change naming results of other environments.
        else:
            self.naming_policies()
        production = results['production'][0]
        workflow = self.review_workflow(production)
        self.say('Review workflow: ' + ', '.join('%s=%d' % (key, len(value)) for key, value in workflow.items()))
        self.elapsed = time.monotonic() - started
        return results, workflow
