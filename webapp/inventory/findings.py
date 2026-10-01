"""Persistent review state, separate from immutable collection snapshots."""
import hashlib
import json
from django.db import transaction
from .diagnostics import LOG
from .models import Environment, Finding, FindingEvent

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
            for kind, applies in checks:
                if applies(row):
                    # Ignore timestamps, cumulative counters and transient exception strings.
                    evidence = {k: row[k] for k in ('configuration', 'configuration_fingerprint', 'unique_id', 'created_at',
                        'rule_id', 'policy_rule_id', 'action', 'disabled', 'source_groups', 'destination_groups',
                        'services', 'scope', 'membership', 'membership_definition', 'usage', 'referenced_by',
                        'hit_status', 'status', 'rule_count', 'vm_usage', 'group_usage', 'group_conditions',
                        'group_assignments', 'other_assignments') if k in row}
                    if 'referenced_by' in evidence:
                        evidence['referenced_by'] = sorted(evidence['referenced_by'])
                    yield kind, row['path'], row.get('name', row['path']), evidence


@transaction.atomic
def synchronize(environment_id):
    """Idempotent; serialize reviews/collection against the latest full snapshot."""
    LOG.info("findings environment_id=%s acquiring environment lock", environment_id)
    environment = Environment.objects.select_for_update().only("id").get(pk=environment_id)
    snapshot = environment.snapshots.filter(testing=False, imported=False).only('id','generated_at').first()
    if snapshot is None:
        return
    if environment.findings.exists() and not environment.findings.exclude(snapshot_id=snapshot.pk).exists():
        return
    existing = {(f.kind, f.path): f for f in environment.findings.all()}
    LOG.info("findings environment_id=%s existing_count=%s", environment_id, len(existing))
    processed = 0
    seen = set()
    for kind, path, name, evidence in candidates(snapshot.report):
        processed += 1
        if processed % 500 == 0:
            LOG.info("findings environment_id=%s processed_candidates=%s", environment_id, processed)
        key = (kind, path)
        seen.add(key)
        digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        finding = existing.get(key)
        if finding is None:
            finding = Finding.objects.create(environment=environment, kind=kind, path=path, name=name[:255],
                evidence=evidence, fingerprint=digest, first_seen=snapshot.generated_at,
                last_seen=snapshot.generated_at, evaluated_at=snapshot.generated_at, snapshot=snapshot)
            FindingEvent.objects.create(finding=finding, message='Finding first observed in a full snapshot.')
            continue
        if digest != finding.fingerprint or not finding.present:
            finding.status = 'open'
            FindingEvent.objects.create(finding=finding, message='Reopened: evidence changed or the finding was observed again. Owner and review date retained.')
        finding.present = True
        finding.name = name[:255]
        finding.evidence, finding.fingerprint = evidence, digest
        finding.last_seen = finding.evaluated_at = snapshot.generated_at
        finding.snapshot = snapshot
        finding.revision += 1
        finding.save()
    for key, finding in existing.items():
        if key not in seen:
            if finding.present:
                FindingEvent.objects.create(finding=finding, message='Not observed in the latest snapshot. This is not proof of resolution; review collection coverage.')
            finding.present = False
            finding.snapshot = snapshot
            finding.evaluated_at = snapshot.generated_at
            finding.revision += 1
            finding.save()

    LOG.info("findings environment_id=%s synchronized candidates=%s absent=%s", environment_id, processed, len(set(existing)-seen))
