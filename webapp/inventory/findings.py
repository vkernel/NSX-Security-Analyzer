"""Persistent review state, separate from immutable collection snapshots."""
import hashlib
import json
from time import perf_counter
from django.db import transaction
from .diagnostics import LOG
from .models import Environment, Finding, FindingEvent, SnapshotFindingAssessment
from . import observation

LABELS = {'unused': 'Unused object candidate', 'empty_group': 'Empty group',
          'membership': 'Unknown group membership', 'zero_hits': 'Zero recorded hits',
          'disabled': 'Disabled rule', 'statistics': 'Unknown rule statistics',
          'empty_policy': 'Empty firewall policy', 'policy': 'Unknown policy inventory',
          'tag': 'Tag usage needs review'}


def candidates(report):
    for rows, checks in (
        (report.get('objects', []), [('unused', lambda r: r.get('usage') == 'unused_candidate'),
            ('empty_group', lambda r: r.get('membership') == 'empty'),
            ('membership', lambda r: r.get('membership') == 'unknown')]),
        (report.get('dfw', {}).get('rules', []), [('zero_hits', lambda r: r.get('hit_status') == 'zero_hits' and not r.get('disabled')),
            ('disabled', lambda r: bool(r.get('disabled'))), ('statistics', lambda r: r.get('hit_status') == 'unknown')]),
        (report.get('dfw', {}).get('policies', []), [('empty_policy', lambda r: r.get('status') == 'empty'),
            ('policy', lambda r: r.get('status') == 'unknown')]),
        (report.get('tags', {}).get('objects', []), [('tag', lambda r: r.get('status') == 'unknown')]),
    ):
        for row in rows:
            if row.get('audit_exclusions'):
                continue
            for kind, applies in checks:
                if applies(row):
                    # Ignore timestamps, cumulative counters and transient exception strings.
                    evidence = {k: row[k] for k in ('configuration', 'configuration_fingerprint', 'unique_id', 'created_at',
                        'rule_id', 'policy_rule_id', 'action', 'disabled', 'source_groups', 'destination_groups',
                        'services', 'scope', 'membership', 'membership_definition', 'usage', 'referenced_by',
                        'hit_status', 'statistics_source', 'status', 'rule_count', 'vm_usage', 'group_usage', 'group_conditions',
                        'group_assignments', 'other_assignments') if k in row}
                    if 'referenced_by' in evidence:
                        evidence['referenced_by'] = sorted(evidence['referenced_by'])
                    yield kind, row['path'], row.get('name', row['path']), evidence


@transaction.atomic
def synchronize(environment_id):
    """Idempotent; serialize reviews/collection against the latest full snapshot."""
    LOG.info("findings environment_id=%s acquiring environment lock", environment_id)
    environment = Environment.objects.select_for_update().only("id", "sync_interval_minutes").get(pk=environment_id)
    snapshot = environment.snapshots.filter(testing=False, imported=False).only('id','generated_at').first()
    if snapshot is None:
        return
    if environment.findings.exists() and not environment.findings.exclude(snapshot_id=snapshot.pk).exists():
        return
    policy = observation.policy_for(environment)
    report = snapshot.report
    source_rows = {r['path']: r for r in report.get('objects', []) + report.get('dfw', {}).get('rules', []) + report.get('dfw', {}).get('policies', [])}
    existing = {(f.kind, f.path): f for f in environment.findings.all()}
    LOG.info("findings environment_id=%s existing_count=%s", environment_id, len(existing))
    started = perf_counter()
    creates, updates, events, assessments = [], [], [], []
    def flush():
        if creates:
            Finding.objects.bulk_create(creates, batch_size=100)
        if updates:
            Finding.objects.bulk_update(updates, ['status', 'present', 'name', 'evidence',
                'fingerprint', 'last_seen', 'evaluated_at', 'snapshot', 'revision'] + observation.FIELDS, batch_size=100)
        if events:
            FindingEvent.objects.bulk_create([FindingEvent(finding=f, message=m) for f,m in events], batch_size=100)
        for f in creates + updates:
            assessments.append(SnapshotFindingAssessment(snapshot=snapshot, path=f.path, kind=f.kind, assessment=observation.summary(f)))
        if assessments:
            SnapshotFindingAssessment.objects.bulk_create(assessments, batch_size=100)
            assessments.clear()
        creates.clear()
        updates.clear()
        events.clear()
    processed = 0
    seen = set()
    for kind, path, name, evidence in candidates(report):
        processed += 1
        if processed % 500 == 0:
            LOG.info("findings environment_id=%s processed_candidates=%s", environment_id, processed)
        key = (kind, path)
        if key in seen:
            continue
        seen.add(key)
        digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        finding = existing.get(key)
        if finding is None:
            finding = Finding(environment=environment, kind=kind, path=path, name=name[:255],
                evidence=evidence, fingerprint=digest, first_seen=snapshot.generated_at,
                last_seen=snapshot.generated_at, evaluated_at=snapshot.generated_at, snapshot=snapshot)
            observation.advance(finding, policy, environment, snapshot.generated_at, reset=True, row=source_rows.get(path, {}))
            creates.append(finding)
            events.append((finding, 'Finding first observed in a full snapshot.'))
            if len(creates) + len(updates) >= 100:
                flush()
            continue
        prior_qualification = finding.qualification
        observation.advance(finding, policy, environment, snapshot.generated_at, reset=digest != finding.fingerprint or not finding.present, row=source_rows.get(path, {}))
        if prior_qualification != finding.qualification:
            events.append((finding, 'Qualification: ' + finding.get_qualification_display()))
        if digest != finding.fingerprint or not finding.present:
            finding.status = 'open'
            events.append((finding, 'Reopened: evidence changed or the finding was observed again. Owner and review date retained.'))
        finding.present = True
        finding.name = name[:255]
        finding.evidence, finding.fingerprint = evidence, digest
        finding.last_seen = finding.evaluated_at = snapshot.generated_at
        finding.snapshot = snapshot
        finding.revision += 1
        updates.append(finding)
        if len(creates) + len(updates) >= 100:
            flush()
    for key, finding in existing.items():
        if key not in seen:
            if finding.present:
                events.append((finding, 'Not observed in the latest snapshot. This is not proof of resolution; review collection coverage.'))
            observation.absent(finding, source_rows)
            finding.present = False
            finding.snapshot = snapshot
            finding.evaluated_at = snapshot.generated_at
            finding.revision += 1
            updates.append(finding)
            if len(creates) + len(updates) >= 100:
                flush()

    flush()
    LOG.info("findings environment_id=%s writes_finished elapsed_seconds=%.3f", environment_id, perf_counter()-started)
    LOG.info("findings environment_id=%s synchronized candidates=%s absent=%s", environment_id, processed, len(set(existing)-seen))
