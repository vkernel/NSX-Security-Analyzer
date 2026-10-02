"""Build immutable, queryable report records once when a snapshot is published."""
import json
import re
import logging
import resource
import sys
from html.parser import HTMLParser

from django.db import transaction, connection
from .models import Snapshot, SnapshotPanel, SnapshotPresentation, SnapshotRecord, SnapshotRecordPanel, SnapshotHistoryData

LABELS = {'referenced':'Referenced', 'unused_candidate':'Unused candidate', 'empty':'Empty',
          'nonempty':'Has members', 'unknown':'Unknown', 'not_applicable':'Not applicable',
          'not_assessed':'Not assessed', 'not_supported':'Not supported', 'zero_hits':'Zero recorded hits',
          'traffic_recorded':'Traffic recorded', 'has_rules':'Has rules', 'both':'VMs and groups',
          'vm_only':'VM use', 'group_only':'Group use', 'other_only':'Other resource use'}


def checkpoint(snapshot_id, stage):
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mib = peak / (1024 * 1024 if sys.platform == 'darwin' else 1024)
    logging.getLogger('inventory.collection').info(
        'Index snapshot=%s stage=%s peak_rss_mib=%.1f', snapshot_id, stage, peak_mib)


def flatten(value):
    if isinstance(value, dict):
        return ' '.join(flatten(v) for v in value.values())
    if isinstance(value, list):
        return ' '.join(flatten(v) for v in value)
    return '' if value is None else str(value)


def columns(view, row):
    identity = row.get('name', '') + '\n' + row.get('path', '')
    if view == 'vms':
        values = [identity, row.get('power_state'), row.get('tag_count'), row.get('group_count')]
    elif view in ('tags', 'scopes'):
        second = row.get('tag_count') if view == 'scopes' else LABELS.get(row.get('status'), row.get('status', ''))
        values = [identity, second, row.get('vm_count'), row.get('group_count'), row.get('other_count', 0)]
    elif view == 'inventory':
        values = [identity, row.get('inventory_type', 'Group' if row.get('kind') == 'group' else 'Custom service'),
                  LABELS.get(row.get('usage'), row.get('usage', '')),
                  LABELS.get(row.get('membership'), row.get('membership', '')) + ' ' + flatten(row.get('membership_definition', {}).get('methods', []))]
    else:
        policy = 'rule_count' in row
        values = [identity, row.get('category') if policy else row.get('policy_name'),
                  LABELS.get(row.get('status' if policy else 'hit_status'), '') + ('' if policy else ' ' + ('Disabled' if row.get('disabled') else 'Enabled')),
                  row.get('rule_count' if policy else 'hit_count')]
    return [str(v) if v is not None else 'Unknown' for v in values] + [json.dumps(row, ensure_ascii=False, sort_keys=True)]


class Panels(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.lines = [0]
        for match in re.finditer('\n', text): self.lines.append(match.end())
        self.stack, self.found = [], []
        self.feed(text)

    def absolute_offset(self):
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag in {'input','br','hr','img','meta','link','wbr','source','area','base','embed','param','col'}: return
        attr = dict(attrs)
        self.stack.append((tag, self.absolute_offset(), attr.get('id') if 'data-panel' in attr else None))

    def handle_endtag(self, tag):
        if not self.stack: return
        opened, start, slug = self.stack.pop()
        if opened != tag: raise ValueError('Invalid generated report markup')
        if slug: self.found.append((start, self.absolute_offset() + len(tag) + 3, slug))


def tag_context(row, metadata):
    result = {'conditions': {}, 'condition_sets': {}, 'firewall_rules': {},
              'firewall_reference_note': metadata.get('firewall_reference_note', '')}
    for key, set_key in [('condition_evidence', 'condition_evidence_set'), ('review_conditions', 'review_condition_set')]:
        ids = row.get(key, [])
        if set_key in row:
            sid = row[set_key]
            ids = metadata.get('condition_sets', [])[sid]
            result['condition_sets'][sid] = ids
        for cid in ids:
            if isinstance(cid, int): result['conditions'][cid] = metadata['conditions'][cid]
    for ref in row.get('firewall_references', []):
        rid = ref['rule']
        result['firewall_rules'][rid] = metadata['firewall_rules'][rid]
    return result


@transaction.atomic
def clear_index(snapshot_id):
    """Delete disposable index rows in SQL, without ORM cascade materialization.

    These derived tables have no application audit events. Delete their
    dependent links first; keep source snapshots and history projections intact.
    """
    Snapshot.objects.select_for_update().only('pk').get(pk=snapshot_id)
    quote = connection.ops.quote_name
    panel = quote(SnapshotPanel._meta.db_table)
    record = quote(SnapshotRecord._meta.db_table)
    links = quote(SnapshotRecordPanel._meta.db_table)
    from .models import SnapshotCoverage, SnapshotCoverageIssue
    with connection.cursor() as cursor:
        cursor.execute(f'DELETE FROM {quote(SnapshotCoverageIssue._meta.db_table)} WHERE coverage_id = %s', [snapshot_id])
        cursor.execute(f'DELETE FROM {quote(SnapshotCoverage._meta.db_table)} WHERE snapshot_id = %s', [snapshot_id])
        cursor.execute(f'DELETE FROM {links} WHERE panel_id IN (SELECT id FROM {panel} WHERE snapshot_id = %s) OR record_id IN (SELECT id FROM {record} WHERE snapshot_id = %s)', [snapshot_id, snapshot_id])
        for model in (SnapshotPanel, SnapshotRecord, SnapshotPresentation):
            cursor.execute(f'DELETE FROM {quote(model._meta.db_table)} WHERE snapshot_id = %s', [snapshot_id])


@transaction.atomic
def build(snapshot, rendered=None):
    """Publish the whole index transactionally. Other requests never see half an index."""
    from .services import engine
    Snapshot.objects.select_for_update().only("id").get(pk=snapshot.pk)
    checkpoint(snapshot.pk, "load_report_start")
    report = snapshot.report
    checkpoint(snapshot.pk, "load_report_complete")
    if not SnapshotHistoryData.objects.filter(snapshot_id=snapshot.pk).exists():
        fields = ('path','name','rule_id','policy_rule_id','unique_id','created_at',
                  'configuration_fingerprint','disabled','hit_status','statistics_checked_at','statistics')
        dfw = snapshot.report.get('dfw', {})
        SnapshotHistoryData.objects.get_or_create(snapshot=snapshot, defaults={'payload':{
            'rules': [{key:row[key] for key in fields if key in row} for row in dfw.get('rules', [])],
            'errors': bool(dfw.get('errors'))}})
    checkpoint(snapshot.pk, "history_projection_complete")
    from .coverage_index import build_coverage
    build_coverage(snapshot, report)
    if SnapshotPresentation.objects.filter(snapshot_id=snapshot.pk).exists(): return
    metadata = {k:v for k,v in snapshot.report.get('tags', {}).items() if k not in {'objects','virtual_machines'}}
    rows, record_ids = [], []
    def flush():
        if not rows: return
        SnapshotRecord.objects.bulk_create(rows, batch_size=50)
        record_ids.extend(row.pk for row in rows)
        rows.clear()
        checkpoint(snapshot.pk, 'records_batch_complete')
        logging.getLogger('inventory.collection').info('Index snapshot=%s records_saved=%d', snapshot.pk, len(record_ids))
    def store_record(ordinal, view, row):
        compact = {key: val for key, val in row.items() if not isinstance(val, (dict, list))}
        compact['referenced_by'] = []
        compact['notes_count'] = len(row.get('notes', []))
        compact['audit_exclusions'] = row.get('audit_exclusions', [])
        if row.get('membership_definition'):
            compact['membership_definition'] = {'methods': row['membership_definition']['methods']}
        if row.get('last_positive_observation'): compact['last_positive_observation'] = row['last_positive_observation']
        sorts = {key: row.get(key) for key in ['power_state','name','path','scope','category','policy_name','rule_count','hit_count','rule_id','policy_rule_id','vm_count','group_count','other_count','tag_count']}
        for key in ['usage','membership','hit_status','status']: sorts[key] = LABELS.get(row.get(key), row.get(key))
        sorts.update(kind=row.get('inventory_type', row.get('kind', '')), method=flatten(row.get('membership_definition', {}).get('methods', [])), references=len(row.get('referenced_by', [])))
        extra = tag_context(row, metadata) if view == 'tags' else {}
        rows.append(SnapshotRecord(snapshot=snapshot, ordinal=ordinal, view=view, name=row['name'], sort_name=row['name'].casefold(),
                    compact=compact, data=row, columns=columns(view, dict(row, resolved_tag_evidence=extra) if extra else row), sort_values=sorts,
                    search_basic=row['name']+' '+row['path'],
                    search_evidence=flatten(row)+' '+flatten(extra)+' '+flatten([LABELS.get(row.get(k), '') for k in ['usage','membership','hit_status','status']])+(' Disabled' if row.get('disabled') else ' Enabled' if 'disabled' in row else '')))
        # Flush incrementally; never accumulate all expanded ORM records.
        if len(rows) >= 50:
            flush()
    # Emit and discard expanded rows during preparation, including VM evidence.
    checkpoint(snapshot.pk, "prepare_layout_start")
    rendered = engine().prepare_index_report(snapshot.report, row_sink=store_record)
    flush()
    checkpoint(snapshot.pk, "prepare_layout_complete")
    rendered = {k:v for k,v in rendered.items() if not k.startswith('_index_')}
    parts, panel_rows = [], {}
    content = rendered['content']
    for start, end, slug in sorted(Panels(content).found):
        html = content[start:end]
        ids = [int(v) for m in re.finditer(r'data-rows="([0-9,]*)"', html) for v in m[1].split(',') if v]
        # A report section currently has one inventory table.
        html = re.sub(r'data-rows="[0-9,]*"', 'data-rows="" data-server-table="'+slug+'"', html)
        panel_rows[slug] = (html, ids)
        heading = re.search(r'<h2[^>]*>(.*?)</h2>', html, re.S)
        title = heading[1] if heading else 'Snapshot overview'
        stub = '<section data-panel data-lazy-panel="true" id="'+slug+'" tabindex="-1"><h2>'+title+'</h2><p role="status">Loading section…</p></section>'
        # Summary panels are small and immediately visible, even on a cold visit.
        parts.append((start, end, html if slug in ('overview', 'dfw-overview') else stub))
    for start, end, stub in reversed(parts): content = content[:start] + stub + content[end:]
    shell = dict(rendered, content=content)
    shell['diagnostics'] = {'dfw': {'collection_diagnostics': snapshot.report.get('dfw', {}).get('collection_diagnostics')},
                            'performance': {'concurrency': {'endpoints': snapshot.report.get('performance', {}).get('concurrency', {}).get('endpoints', {})}}}
    for slug, (html, ids) in panel_rows.items():
        panel = SnapshotPanel.objects.create(snapshot=snapshot, slug=slug, html=html)
        for offset in range(0, len(ids), 1000):
            SnapshotRecordPanel.objects.bulk_create([
                SnapshotRecordPanel(panel=panel, record_id=record_ids[i])
                for i in ids[offset:offset+1000]], batch_size=1000)
    checkpoint(snapshot.pk, "save_presentation_start")
    SnapshotPresentation.objects.create(snapshot=snapshot, shell=shell, tag_evidence=metadata)
    checkpoint(snapshot.pk, "save_presentation_complete")
