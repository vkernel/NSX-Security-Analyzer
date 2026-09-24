"""Compare saved evidence only; missing fields are not equivalent to empty fields."""
import json


def records(report):
    inventory = report.get('inventory', {})
    result = {}
    for kind, rows in (
        ('Group', inventory.get('groups', [r for r in report.get('objects', []) if r.get('kind') == 'group'])),
        ('Service', inventory.get('services', [r for r in report.get('objects', []) if r.get('kind') == 'custom_service'])),
        ('Rule', report.get('dfw', {}).get('rules', [])),
    ):
        for row in rows:
            fields = {key: row[key] for key in ('name', 'configuration', 'configuration_fingerprint', 'unique_id',
                'created_at', 'rule_id', 'policy_rule_id', 'action', 'disabled', 'source_groups',
                'destination_groups', 'services', 'scope', 'category', 'policy_path',
                'membership', 'membership_definition', 'tags') if key in row}
            # The full configuration already explains this opaque digest.
            if 'configuration' in fields:
                fields.pop('configuration_fingerprint', None)
            result[(kind, row['path'])] = fields
    return result


def differences(before, after, prefix=''):
    for key in sorted(before.keys() | after.keys()):
        label = f'{prefix}.{key}' if prefix else key
        if key not in before or key not in after:
            yield {'field': label, 'before': json.dumps(before[key], ensure_ascii=False) if key in before else 'Not recorded',
                   'after': json.dumps(after[key], ensure_ascii=False) if key in after else 'Not recorded', 'unknown': True}
        elif isinstance(before[key], dict) and isinstance(after[key], dict):
            yield from differences(before[key], after[key], label)
        elif before[key] != after[key]:
            yield {'field': label, 'before': json.dumps(before[key], ensure_ascii=False),
                   'after': json.dumps(after[key], ensure_ascii=False),
                   'unknown': key == 'membership' and (before[key] in ('unknown', 'not_assessed') or after[key] in ('unknown', 'not_assessed'))}


def compare(before, after):
    left, right = records(before), records(after)
    rows = []
    for key in sorted(left.keys() | right.keys()):
        old, new = left.get(key), right.get(key)
        kind, path = key
        if old is None or new is None:
            # Inventory omissions under partial coverage do not establish creation/deletion.
            complete = 'inventory' in before and 'inventory' in after if kind != 'Rule' else (
                'dfw' in before and 'dfw' in after and not before['dfw'].get('errors') and not after['dfw'].get('errors'))
            status = ('Added' if old is None else 'Removed') if complete else 'Presence uncertain'
            changes = []
        else:
            changes = list(differences(old, new))
            if not changes:
                continue
            status = 'Evidence incomplete' if all(c['unknown'] for c in changes) else 'Changed'
        rows.append({'kind': kind, 'path': path, 'name': (new or old).get('name', path), 'status': status, 'changes': changes})
    return rows
