"""Replay bounded historical finding evidence without changing saved assessments."""
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
    for environment_id in environment_ids:
        with transaction.atomic():
            row, _ = FindingRecalculation.objects.select_for_update().get_or_create(environment_id=environment_id)
            row.kinds = sorted(set(kinds) | (set(row.kinds) if row.status in ('queued', 'running', 'failed') else set()))
            row.token = uuid.uuid4()
            row.status = 'queued'
            row.started_at = row.finished_at = None
            row.processed = row.total = row.eligible = 0
            row.error = ''
            row.save()


def claim():
    FindingRecalculation.objects.filter(status='running', started_at__lt=timezone.now()-timedelta(seconds=settings.AUDIT_TIMEOUT+60)).update(
        status='failed', error='Recalculation worker stopped or exceeded its time limit. Save criteria to retry.')
    with transaction.atomic():
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
    if SnapshotFindingEvidenceIndex.objects.filter(snapshot_id=snapshot_id, version=2).exists(): return
    SnapshotFindingEvidence.objects.filter(snapshot_id=snapshot_id).delete()
    batch = []
    for section, row in source_rows(snapshot_id):
        report = {'objects': [row]} if section == 'objects' else {'dfw': {section: [row]}}
        for kind, path, name, evidence in candidates(report):
            if kind not in observation.KINDS: continue
            digest = observation.condition_fingerprint(kind, evidence)
            batch.append(SnapshotFindingEvidence(snapshot_id=snapshot_id, kind=kind, path=path, name=name[:255],
                fingerprint=digest, zero_counter=row.get('hit_count') == 0,
                checked_at=str(row.get('statistics_checked_at') or '')[:64]))
        if len(batch) >= 100:
            SnapshotFindingEvidence.objects.bulk_create(batch, batch_size=100, ignore_conflicts=True)
            batch.clear()
    if batch: SnapshotFindingEvidence.objects.bulk_create(batch, batch_size=100, ignore_conflicts=True)
    SnapshotFindingEvidenceIndex.objects.update_or_create(snapshot_id=snapshot_id, defaults={'version': 2})


def policy_values(policy):
    return tuple(getattr(policy, field) for field in [k+'_days' for k in observation.KINDS] + ['minimum_observations', 'maximum_gap_hours'])


def run(pk, token):
    job = FindingRecalculation.objects.get(pk=pk, token=token, status='running')
    env = Environment.objects.only('pk', 'sync_interval_minutes').get(pk=job.environment_id)
    policy = observation.policy_for(env)
    signature = policy_values(policy)
    history = list(snapshots(env.pk).values_list('pk', 'generated_at'))
    if all(getattr(policy, kind+'_days') == 0 for kind in job.kinds):
        history = history[-1:]
    current = FindingRecalculation.objects.filter(pk=pk, token=token, status='running')
    if not current.update(total=len(history)): return
    states = {}
    for number, (snapshot_id, stamp) in enumerate(history, 1):
        if not current.exists(): return
        index(snapshot_id)
        seen = set()
        rows = SnapshotFindingEvidence.objects.filter(snapshot_id=snapshot_id, kind__in=job.kinds).order_by('pk')
        for row in rows.iterator(chunk_size=200):
            key = (row.kind, row.path)
            seen.add(key)
            state = states.get(key)
            new = state is None
            if new:
                state = Finding(environment=env, kind=row.kind, path=row.path, first_seen=stamp, last_seen=stamp)
            reset = new or not state.present or state.fingerprint != row.fingerprint
            observation.advance(state, policy, env, stamp, reset=reset,
                                row={'hit_count': 0 if row.zero_counter else None, 'statistics_checked_at': row.checked_at})
            state.name, state.fingerprint = row.name, row.fingerprint
            state.last_seen = state.evaluated_at = stamp
            state.snapshot_id, state.present = snapshot_id, True
            states[key] = state
        # Missing/excluded/unknown objects break the sequence; never bridge them.
        for key in list(states):
            if key not in seen: del states[key]
        current.update(processed=number)
        LOG.info('finding_recalculation job=%s snapshot=%s progress=%s/%s candidates=%s', pk, snapshot_id, number, len(history), len(states))
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
        # Restore evidence and presence for rows missing from the live finding state.
        restore = {key for key in states if key not in existing or not existing[key].present}
        restored = {}
        if restore and history:
            for section, row in source_rows(history[-1][0]):
                report = {'objects': [row]} if section == 'objects' else {'dfw': {section: [row]}}
                for kind, path, name, evidence in candidates(report):
                    if (kind, path) in restore: restored[(kind, path)] = evidence
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
                creates.append(state)
        for target in existing.values():
            target.qualification = 'insufficient'
            target.qualification_reason = 'No confirmed condition in the latest retained full collection.'
            target.observation_started = target.qualified_at = None
            target.observation_count = target.observation_days = 0
            target.required_days = getattr(policy, target.kind+'_days')
            target.policy_fingerprint = ''
            target.revision += 1
            updates.append(target)
        for offset in range(0, len(updates), 100):
            Finding.objects.bulk_update(updates[offset:offset+100], observation.FIELDS+['revision'], batch_size=100)
        for offset in range(0, len(restored_updates), 100):
            Finding.objects.bulk_update(restored_updates[offset:offset+100], ['present', 'name', 'last_seen', 'evaluated_at', 'snapshot', 'evidence', 'fingerprint'], batch_size=100)
        if creates: Finding.objects.bulk_create(creates, batch_size=100)
        eligible = Finding.objects.filter(environment=env, present=True, qualification='eligible').count()
        current.update(status='completed', finished_at=timezone.now(), eligible=eligible)
        LOG.info('finding_recalculation job=%s completed eligible=%s', pk, eligible)
