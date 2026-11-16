"""Per-category export columns, presets and validation.

The evidence-basis column is always included, so a narrowed export still tells direct
VM associations, interface attachments, IP/subnet matches and saved configuration apart.
"""
import json

FIREWALL_SECTIONS = ('firewall', 'confirmed_rules', 'firewall_references')
NONE = {'context_profiles': 'None', 'applied_to': 'DFW'}
BASIS = {'direct_vm': 'Direct VM association', 'network_attachment': 'Interface attachment', 'ip_subnet': 'IP/subnet match'}
SECTION_BASIS = {'objects': 'Saved reference', 'vms': 'Tag on VM (metadata, not membership)',
                 'group_conditions': 'Group membership condition', 'group_assignments': 'Tag on group (metadata)',
                 'other_assignments': 'Tag on other resource (metadata)', 'tags': 'Tag in this scope'}


def text(value):
    if value is None: return ''
    if isinstance(value, (dict, list)): return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def order(item):
    return item.get('nsx_order') if isinstance(item.get('nsx_order'), dict) else {}


def rule_field(item, name):
    field = (item.get('rule_fields') or {}).get(name)
    if not field or not field.get('recorded'): return ''
    if field.get('any'): return NONE.get(name, 'Any')
    values = [entry.get('name') or entry.get('path') for entry in field.get('items', [])]
    if field.get('total', len(values)) > len(values): values.append('+%d more' % (field['total'] - len(values)))
    prefix = ('From policy: ' if field.get('origin') == 'policy' else '') + ('NOT ' if field.get('excluded') else '')
    return prefix + '; '.join(text(value) for value in values)


def matched_in(item):
    label = lambda group: text(group.get('name', group.get('path'))) if isinstance(group, dict) else text(group)
    matches = item.get('field_matches', [])
    direct = {match.get('group', {}).get('path') for match in matches}
    indirect = [group for group in item.get('matched_groups', item.get('via_groups', []))
                if (group.get('path') if isinstance(group, dict) else group) not in direct]
    # Field/object pairs, plus matched objects whose field was not retained.
    return '; '.join([match.get('field', '') + ': ' + label(match.get('group', {})) for match in matches] +
                     [('Field not retained: ' if matches else '') + label(group) for group in indirect])


def reason(item):
    value = '; '.join(text(item[key]) for key in ('reference_reason', 'membership_basis', 'membership_source', 'network_checked_at', 'ip_checked_at', 'field_note') if item.get(key))
    if item.get('via'): value += '; Interface links: ' + text(item['via'])
    if item.get('ip_matches'): value += '; IP matches: ' + text(item['ip_matches'])
    return value


def basis(item, section):
    raw = item.get('membership_basis') or item.get('reference_basis')
    if raw:
        label = BASIS.get(raw, text(raw).replace('_', ' ').capitalize())
        return label + (' (current NSX)' if item.get('membership_source') == 'current_nsx' else '')
    if section.get('key') in FIREWALL_SECTIONS:
        return text(item.get('reference_reason')) or 'Saved configuration'
    return SECTION_BASIS.get(section.get('key'), 'Saved configuration')


def checked(item, section):
    return text(item.get('network_checked_at') or item.get('ip_checked_at') or item.get('observed_at') or item.get('checked_at') or section.get('checked_at'))


def interfaces(item):
    links = item.get('via') if isinstance(item.get('via'), list) else []
    return '; '.join(' · '.join(part for part in (text(link.get('segment_path', '')).rsplit('/', 1)[-1], text(link.get('port_path', '')).rsplit('/', 1)[-1],
                                                    text(link.get('interface_id'))) if part) for link in links if isinstance(link, dict))


def ip_matches(item):
    matches = item.get('ip_matches') if isinstance(item.get('ip_matches'), list) else []
    return '; '.join(text(match.get('ip')) + (' in ' + text(match.get('entry')) if match.get('entry') else '') for match in matches if isinstance(match, dict))


def details(item, known):
    return '; '.join(key.replace('_', ' ') + ': ' + text(value) for key, value in sorted(item.items())
                     if key not in known and value not in (None, '', [], {}))


def column(key, label, value, essential=True, required=False):
    return {'key': key, 'label': label, 'value': value, 'essential': essential, 'required': required}


FIREWALL = [
    column('category', 'Rule category', lambda i, s: order(i).get('category', i.get('category'))),
    column('policy', 'Policy', lambda i, s: i.get('policy_name') or order(i).get('policy')),
    column('policy_sequence', 'Policy sequence', lambda i, s: order(i).get('policy_sequence')),
    column('rule_sequence', 'Rule sequence', lambda i, s: order(i).get('rule_sequence', i.get('sequence_number'))),
    column('name', 'Name', lambda i, s: i.get('name', i.get('tag', ''))),
    column('rule_id', 'Rule ID', lambda i, s: i.get('rule_id')),
    column('path', 'Path', lambda i, s: i.get('path'), essential=False),
    column('sources', 'Sources', lambda i, s: rule_field(i, 'sources')),
    column('destinations', 'Destinations', lambda i, s: rule_field(i, 'destinations')),
    column('services', 'Services', lambda i, s: rule_field(i, 'services')),
    column('context_profiles', 'Context profiles', lambda i, s: rule_field(i, 'context_profiles'), essential=False),
    column('applied_to', 'Applied to', lambda i, s: rule_field(i, 'applied_to')),
    column('action', 'Action', lambda i, s: i.get('action')),
    column('state', 'State', lambda i, s: 'Disabled' if i.get('disabled') is True else 'Enabled' if i.get('disabled') is False else ''),
    column('evidence_basis', 'Evidence basis', basis, required=True),
    column('matched_in', 'Matched in', lambda i, s: matched_in(i), essential=False),
    column('reason', 'Reason / evidence', lambda i, s: reason(i), essential=False),
]
NAME = column('name', 'Name', lambda i, s: i.get('name') or i.get('tag') or i.get('path'))
PATH = column('path', 'Path / identity', lambda i, s: i.get('path', i.get('external_id', i.get('identity'))))
BASIS_COLUMN = column('evidence_basis', 'Evidence basis', basis, required=True)
CHECKED = column('checked_at', 'Checked at', checked, essential=False)
OBJECT_KNOWN = {'name', 'path', 'tag', 'external_id', 'identity', 'membership_basis', 'reference_basis', 'membership_source',
                'network_checked_at', 'ip_checked_at', 'observed_at', 'checked_at', 'via', 'ip_matches', 'ip_match_count', 'status', 'via_group'}
DETAILS = column('details', 'Details', lambda i, s: details(i, OBJECT_KNOWN), essential=False)
OBJECTS = {
    'resolved_groups': [NAME, PATH, BASIS_COLUMN, column('status', 'Status', lambda i, s: i.get('status'), essential=False), CHECKED, DETAILS],
    'network_groups': [NAME, PATH, BASIS_COLUMN, column('interfaces', 'Interface links', lambda i, s: interfaces(i)), CHECKED, DETAILS],
    'ip_groups': [NAME, PATH, BASIS_COLUMN, column('ip_matches', 'IP matches', lambda i, s: ip_matches(i)),
                  column('match_count', 'Match count', lambda i, s: i.get('ip_match_count'), essential=False), CHECKED, DETAILS],
    'objects': [NAME, PATH, BASIS_COLUMN, column('via_group', 'Referenced through', lambda i, s: i.get('via_group'), essential=False), DETAILS],
}
GENERIC = [NAME, PATH, BASIS_COLUMN, CHECKED, DETAILS]


def schema(section_key):
    return FIREWALL if section_key in FIREWALL_SECTIONS else OBJECTS.get(section_key, GENERIC)


def preset(section_key, name):
    columns = schema(section_key)
    return [c['key'] for c in columns if name == 'full' or c['essential'] or c['required']]


def normalize(section_key, keys):
    """Selected keys in schema order, with required columns always present."""
    columns = schema(section_key)
    allowed = {c['key'] for c in columns}
    if not isinstance(keys, list) or not keys or len(keys) > len(columns) or any(not isinstance(k, str) or k not in allowed for k in keys):
        raise ValueError('Choose columns from the list for each category.')
    wanted = set(keys) | {c['key'] for c in columns if c['required']}
    return [c['key'] for c in columns if c['key'] in wanted]


def table(section, keys):
    columns = [c for c in schema(section['key']) if c['key'] in keys]
    return [c['label'] for c in columns], [[text(c['value'](item, section)) if isinstance(item, dict) else text(item) for c in columns]
                                           for item in section['items']]


def describe(sections):
    """Column choices for the export dialog, per category key."""
    return {key: [{'key': c['key'], 'label': c['label'], 'essential': c['essential'], 'required': c['required']} for c in schema(key)]
            for key in sections}
