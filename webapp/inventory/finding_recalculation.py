"""Replay bounded historical finding evidence without changing saved assessments."""
from .approval_modes import required
import hashlib
import json
import uuid
from datetime import timedelta
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from . import observation
from .findings import candidates
from .models import (Environment, Finding, FindingPolicy, Snapshot, FindingRecalculation,
                     SnapshotFindingEvidence, SnapshotFindingEvidenceIndex)
from .diagnostics import LOG


def snapshots(environment_id):
    return Snapshot.objects.filter(environment_id=environment_id, testing=False, imported=False).filter(
        Q(job__isnull=True) | Q(job__status='succeeded')).order_by('generated_at', 'created_at', 'pk')


def queue(environment_ids, kinds):
    for environment_id in sorted(set(environment_ids)):
        with transaction.atomic():
            # Serialize criteria changes with review decisions and replay commits.
            Environment.objects.select_for_update().only('pk').get(pk=environment_id)
            row, _ = FindingRecalculation.objects.select_for_update().get_or_create(environment_id=environment_id)
            row.kinds = sorted(set(kinds) | (set(row.kinds) if row.status in ('queued', 'running', 'failed') else set()))
            row.token = uuid.uuid4()
            row.status = 'queued'
            row.started_at = row.finished_at = None
            row.processed = row.total = row.eligible = row.paused = row.recalculated = 0
            row.error = ''
            row.save()


def claim():
    FindingRecalculation.objects.filter(status='running', started_at__lt=timezone.now()-timedelta(seconds=settings.AUDIT_TIMEOUT+60)).update(
        status='failed', error='Recalculation worker stopped or exceeded its time limit. Save criteria to retry.')
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(781249313)')
        if FindingRecalculation.objects.filter(status='running').exists():
            return None
        rows = FindingRecalculation.objects.filter(status='queued').order_by('requested_at')
        rows = rows.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else rows.select_for_update()
        row = rows.first()
        if row:
            row.status, row.started_at = 'running', timezone.now()
            row.save(update_fields=['status', 'started_at'])
        return row


def fail(pk, token):
    FindingRecalculation.objects.filter(pk=pk, token=token, status='running').update(
        status='failed', finished_at=timezone.now(), error='Recalculation failed or was interrupted. See worker diagnostics; save criteria to retry.')


def source_rows(snapshot_id):
    """Stream JSONB array entries, not entire reports/HTML, for legacy snapshots."""
    if connection.vendor != 'postgresql':
        report = Snapshot.objects.values_list('report', flat=True).get(pk=snapshot_id)
        for section, rows in [('objects', report.get('objects', [])), ('rules', report.get('dfw', {}).get('rules', [])),
                              ('policies', report.get('dfw', {}).get('policies', []))]:
            for row in rows: yield section, row
        return
    # Named cursor prevents psycopg buffering all inventory rows in the client.
    with connection.connection.cursor(name='finding_replay_' + uuid.uuid4().hex) as cursor:
        cursor.execute('''SELECT section, item FROM inventory_snapshot s
            CROSS JOIN LATERAL (VALUES ('objects', s.report->'objects'),
                ('rules', s.report#>'{dfw,rules}'), ('policies', s.report#>'{dfw,policies}')) v(section, items)
            CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(items)='array' THEN items ELSE '[]'::jsonb END) item
            WHERE s.id=%s''', [snapshot_id])
        while True:
            batch = cursor.fetchmany(50)
            if not batch: break
            for section, row in batch:
                yield section, json.loads(row) if isinstance(row, str) else row


@transaction.atomic
def index(snapshot_id):
    Snapshot.objects.select_for_update().only('pk').get(pk=snapshot_id)
    if SnapshotFindingEvidenceIndex.objects.filter(snapshot_id=snapshot_id, version=3).exists(): return
    SnapshotFindingEvidence.objects.filter(snapshot_id=snapshot_id).delete()
    batch = []
    for section, row in source_rows(snapshot_id):
        report = {'objects': [row]} if section == 'objects' else {'dfw': {section: [row]}}
        for kind, path, name, evidence in candidates(report):
            if kind not in observation.KINDS: continue
            from .transient import condition_scope
            digest = observation.condition_fingerprint(kind, evidence)
            batch.append(SnapshotFindingEvidence(snapshot_id=snapshot_id, kind=kind, path=path, name=name[:255],
                fingerprint=digest, membership_scope=condition_scope(row) or '', zero_counter=row.get('hit_count') == 0,
                checked_at=str(row.get('statistics_checked_at') or '')[:64]))
        if len(batch) >= 100:
            SnapshotFindingEvidence.objects.bulk_create(batch, batch_size=100, ignore_conflicts=True)
            batch.clear()
    if batch: SnapshotFindingEvidence.objects.bulk_create(batch, batch_size=100, ignore_conflicts=True)
    SnapshotFindingEvidenceIndex.objects.update_or_create(snapshot_id=snapshot_id, defaults={'version': 3})


def policy_values(policy):
    return tuple(getattr(policy, field) for field in [k+'_days' for k in observation.KINDS] + ['minimum_observations', 'maximum_gap_hours']) + tuple((r.pk, r.name, r.tag_scope, r.reason, r.mode, r.empty_days, r.minimum_observations, r.maximum_gap_hours) for r in getattr(policy, 'transient_rules', []))


def run(pk, token):
    job = FindingRecalculation.objects.filter(pk=pk, token=token, status='running').first()
    if not job: return
    env = Environment.objects.only('pk', 'sync_interval_minutes').get(pk=job.environment_id)
    policy = observation.policy_for(env)
    signature = policy_values(policy)
    history = list(snapshots(env.pk).values_list('pk', 'generated_at'))
    if all(getattr(policy, kind+'_days') == 0 for kind in job.kinds) and not ('empty_group' in job.kinds and any(r.mode == 'monitor' for r in policy.transient_rules)):
        history = history[-1:]
    current = FindingRecalculation.objects.filter(pk=pk, token=token, status='running')
    if not current.update(total=len(history)): return
    from .usability import policy as workspace_policy
    approval_settings = workspace_policy()
    from .evidence_coverage import snapshot_issues, relevant
    states = {}
    latest_coverage = []
    for number, (snapshot_id, stamp) in enumerate(history, 1):
        if not current.exists(): return
        latest_coverage = snapshot_issues(Snapshot.objects.only('pk', 'summary').get(pk=snapshot_id))
        # Unverified observations cannot establish continuity across a coverage gap.
        states = {key: state for key, state in states.items() if not relevant(latest_coverage, *key)}
        index(snapshot_id)
        seen = set()
        rows = SnapshotFindingEvidence.objects.filter(snapshot_id=snapshot_id, kind__in=job.kinds).order_by('pk')
        for row in rows.iterator(chunk_size=200):
            if relevant(latest_coverage, row.kind, row.path):
                continue
            key = (row.kind, row.path)
            seen.add(key)
            state = states.get(key)
            new = state is None
            if new:
                state = Finding(environment=env, required_approvals=required(row.kind, approval_settings), kind=row.kind, path=row.path, first_seen=stamp, last_seen=stamp)
            reset = new or not state.present or state.fingerprint != row.fingerprint
            observation.advance(state, policy, env, stamp, reset=reset,
                                row={'hit_count': 0 if row.zero_counter else None, 'statistics_checked_at': row.checked_at, '_membership_scope': row.membership_scope})
            state.name, state.fingerprint = row.name, row.fingerprint
            state.last_seen = state.evaluated_at = stamp
            state.snapshot_id, state.present = snapshot_id, True
            states[key] = state
        # Missing/excluded/unknown objects break the sequence; never bridge them.
        for key in list(states):
            if key not in seen and not relevant(latest_coverage, *key): del states[key]
        current.update(processed=number)
        LOG.info('finding_recalculation job=%s snapshot=%s progress=%s/%s candidates=%s', pk, snapshot_id, number, len(history), len(states))
    # Prepare potentially large evidence projections before taking the publication
    # lock. Recheck the snapshot, policy and restore set after acquiring it.
    states = {key: state for key, state in states.items() if not relevant(latest_coverage, *key)}
    present_keys = set(Finding.objects.filter(environment=env, kind__in=job.kinds, present=True).values_list('kind', 'path'))
    restore_candidates = set(states) - present_keys
    restored = {}
    if restore_candidates and history:
        for section, row in source_rows(history[-1][0]):
            report = {'objects': [row]} if section == 'objects' else {'dfw': {section: [row]}}
            for kind, path, name, evidence in candidates(report):
                if (kind, path) in restore_candidates:
                    restored[(kind, path)] = evidence
    with transaction.atomic():
        # Same environment lock as collection synchronization and finding reviews.
        Environment.objects.select_for_update().get(pk=env.pk)
        locked = FindingRecalculation.objects.select_for_update().get(pk=pk)
        if str(locked.token) != str(token) or locked.status != 'running': return
        latest = snapshots(env.pk).only('pk').last()
        fresh_env = Environment.objects.only('pk', 'sync_interval_minutes').get(pk=env.pk)
        if ((latest.pk if latest else None) != (history[-1][0] if history else None)
                or policy_values(observation.policy_for(env)) != signature
                or fresh_env.sync_interval_minutes != env.sync_interval_minutes):
            current.update(status='queued', processed=0)
            return
        existing = {(f.kind, f.path): f for f in Finding.objects.filter(environment=env, kind__in=job.kinds).defer('evidence')}
        from .deleted_objects import identities, superseded
        deleted = identities(env.pk)
        for key in list(states):
            target = existing.get(key)
            evidence = restored.get(key, target.evidence if target and key[1] in deleted else {})
            if superseded(deleted, key[1], evidence):
                states.pop(key)
                existing.pop(key, None)
        # Restore evidence and presence for rows missing from the live finding state.
        restore = {key for key in states if key not in existing or not existing[key].present}
        if not restore.issubset(restored):
            # A concurrent publication changed the rows needing restoration.
            current.update(status='queued', processed=0)
            return
        approval_settings = workspace_policy()
        creates, updates, restored_updates = [], [], []
        for key, state in states.items():
            target = existing.pop(key, None)
            if target:
                # Preserve collected evidence, review decisions, owners and notes.
                for field in observation.FIELDS: setattr(target, field, getattr(state, field))
                if key in restore:
                    restored_updates.append(target)
                    target.present = True
                    target.name, target.last_seen, target.evaluated_at = state.name, state.last_seen, state.evaluated_at
                    target.snapshot_id = state.snapshot_id
                    target.evidence = restored[key]
                    target.fingerprint = hashlib.sha256(json.dumps(target.evidence, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                target.revision += 1
                updates.append(target)
            else:
                state.evidence = restored[key]
                state.fingerprint = hashlib.sha256(json.dumps(state.evidence, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                state.required_approvals = required(state.kind, approval_settings)
                creates.append(state)
        paused = 0
        for target in existing.values():
            if relevant(latest_coverage, target.kind, target.path):
                paused += 1
                from .transient import apply
                rules = getattr(policy, 'transient_rules', [])
                evidence = target.evidence
                if not apply(target, evidence, rules):
                    _, monitor, days, _, signature = observation.criteria(target.kind, policy, env, evidence)
                    if target.policy_fingerprint == signature and target.required_days == (days or 0):
                        # A coverage warning alone must not discard unchanged review decisions.
                        continue
                    target.required_days = days or 0
                    target.qualification = 'insufficient'
                    prefix = 'Prolonged empty VDI group — ' + monitor.name + ': ' if monitor else ''
                    target.qualification_reason = (prefix + 'Criteria changed; verification paused. Current requirement: ' + str(days or 0) + ' days. Relevant collection evidence is incomplete.')[:255]
                    target.observation_started = target.qualified_at = None
                    target.observation_count = target.observation_days = 0
                    target.policy_fingerprint = signature
                target.revision += 1
                updates.append(target)
                continue
            target.qualification = 'insufficient'
            target.qualification_reason = 'No confirmed condition in the latest retained full collection.'
            target.observation_started = target.qualified_at = None
            target.observation_count = target.observation_days = 0
            target.required_days = getattr(policy, target.kind+'_days')
            target.policy_fingerprint = ''
            target.revision += 1
            updates.append(target)
        from .finding_workflow import invalidate, FIELDS as WORKFLOW_FIELDS
        from .transient import apply
        suppression_targets = [target for target in creates + updates if target.kind == 'empty_group' and (target.kind, target.path) in states]
        for offset in range(0, len(suppression_targets), 100):
            batch = suppression_targets[offset:offset+100]
            evidence = dict(Finding.objects.filter(pk__in=[t.pk for t in batch if t.pk]).values_list('pk', 'evidence'))
            for target in batch:
                apply(target, target.__dict__.get('evidence', evidence.get(target.pk, {})), getattr(policy, 'transient_rules', []))
        for target in updates:
            if not target.present or target.qualification != 'eligible':
                invalidate(target, 'Recalculated criteria no longer qualify this finding.')
        for offset in range(0, len(updates), 100):
            Finding.objects.bulk_update(updates[offset:offset+100], observation.FIELDS+['revision']+WORKFLOW_FIELDS, batch_size=100)
        for offset in range(0, len(restored_updates), 100):
            Finding.objects.bulk_update(restored_updates[offset:offset+100], ['present', 'name', 'last_seen', 'evaluated_at', 'snapshot', 'evidence', 'fingerprint'], batch_size=100)
        if creates: Finding.objects.bulk_create(creates, batch_size=100)
        eligible = Finding.objects.filter(environment=env, present=True, qualification='eligible').count()
        current.update(status='completed', finished_at=timezone.now(), eligible=eligible, paused=paused, recalculated=len(states))
        LOG.info('finding_recalculation job=%s completed eligible=%s', pk, eligible)
