"""Project only requested tag evidence out of PostgreSQL JSONB."""
from .models import SnapshotPresentation


def project(snapshot_id, paths):
    result = {}
    paths = list(dict.fromkeys(paths))
    for offset in range(0, len(paths), 200):
        batch = paths[offset:offset+200]
        fields = ['tag_evidence__'+path for path in batch]
        row = SnapshotPresentation.objects.filter(snapshot_id=snapshot_id).values(*fields).get()
        result.update({path:row[field] for path,field in zip(batch,fields)})
    return result


def for_tag(snapshot_id, row):
    result = {'conditions':{},'condition_sets':{},'firewall_rules':{}}
    sets = [row[k] for k in ('condition_evidence_set','review_condition_set') if k in row]
    initial = project(snapshot_id, ['firewall_reference_note']+['condition_sets__'+str(i) for i in sets])
    result['firewall_reference_note'] = initial['firewall_reference_note'] or ''
    ids = []
    for key,set_key in [('condition_evidence','condition_evidence_set'),('review_conditions','review_condition_set')]:
        if set_key in row:
            sid = row[set_key]
            values = initial['condition_sets__'+str(sid)] or []
            result['condition_sets'][sid] = values
        else:
            values = row.get(key, [])
        ids.extend(i for i in values if type(i) is int)
    rules = {ref['rule'] for ref in row.get('firewall_references', [])}
    fields = ['conditions__'+str(i) for i in set(ids)]+['firewall_rules__'+str(i) for i in rules]
    values = project(snapshot_id, fields)
    for field,value in values.items():
        kind,ordinal = field.split('__')
        result[kind][int(ordinal)] = value
    return result


def coverage(snapshot_id):
    result = project(snapshot_id, ['unsupported_conditions','unmatched_conditions'])
    ids = {i for values in result.values() for i in (values or []) if type(i) is int}
    values = project(snapshot_id, ['conditions__'+str(i) for i in ids])
    result['conditions'] = {int(field.split('__')[1]):value for field,value in values.items()}
    return result
