"""Conservative evidence scopes. Unknown failures are never treated as unrelated."""
import re

ALL = {'unused', 'unused_service', 'empty_group', 'membership', 'zero_hits',
       'disabled', 'statistics', 'empty_policy', 'policy', 'tag'}
VM = {'empty_group', 'membership', 'tag'}
FIREWALL = {'unused', 'unused_service', 'zero_hits', 'disabled', 'statistics', 'empty_policy', 'policy'}


def inventory_issue(warning):
    warning = warning if isinstance(warning, dict) else {}
    endpoint = warning.get('endpoint', '')
    kinds, path, scope = ALL, '', 'Unclassified inventory'
    if endpoint == '/infra/realized-state/virtual-machines':
        kinds, scope = VM, 'VM inventory'
    elif re.fullmatch(r'/infra/domains/[^/]+/groups/[^/]+/members/(virtual-machines|ip-addresses|logical-ports|logical-switches)', endpoint):
        kinds, path, scope = VM, endpoint.split('/members/')[0], 'Group membership'
    elif re.fullmatch(r'/infra/domains/[^/]+/security-policies/[^/]+/rules', endpoint):
        kinds, scope = FIREWALL, 'Firewall rule inventory'
    return {'scope': scope, 'endpoint': endpoint, 'path': path,
            'kinds': sorted(kinds), 'detail': str(warning.get('detail') or 'Inventory verification incomplete.')}


def issues(report):
    result = [inventory_issue(w) for w in report.get('inventory_warnings', [])]
    for section, kinds in [('dfw', FIREWALL), ('tags', VM)]:
        for error in report.get(section, {}).get('errors', []):
            result.append({'scope': section, 'endpoint': '', 'path': '', 'kinds': sorted(kinds), 'detail': str(error)})
    search = report.get('search_coverage') or {}
    if search.get('errors') or search.get('failed'):
        result.append({'scope':'Reference search', 'endpoint':'/search/query', 'path':'',
                       'kinds':['unused','unused_service'], 'detail':'Reference search failed or is incomplete.'})
    return result


def relevant(entries, kind, path):
    return [entry for entry in entries if kind in entry.get('kinds', ALL)
            and (not entry.get('path') or entry['path'] == path or kind == 'tag')]


def snapshot_issues(snapshot):
    """Support older snapshots without assuming a missing projection means complete."""
    from .models import Snapshot
    summary = snapshot.summary or {}
    if 'evidence_coverage' in summary and (summary['evidence_coverage'] or not summary.get('inventory_warnings')):
        return summary['evidence_coverage']
    data = Snapshot.objects.filter(pk=snapshot.pk).values(
        'report__inventory_warnings', 'report__dfw__errors', 'report__tags__errors', 'report__search_coverage').get()
    report = {'inventory_warnings':data['report__inventory_warnings'] or [],
              'dfw':{'errors':data['report__dfw__errors'] or []},
              'tags':{'errors':data['report__tags__errors'] or []},
              'search_coverage':data['report__search_coverage'] or {}}
    if summary.get('inventory_warnings') and not report['inventory_warnings']:
        report['inventory_warnings'] = [{'detail':'Inventory verification flagged; endpoint details unavailable.'}]
    return issues(report)
