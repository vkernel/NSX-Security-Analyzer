"""Offline request-volume and preparation comparison, not a production ETA.

Run: python benchmarks/saved_vm_associations.py
No HTTP, database, credentials, or production inventory is used.
"""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import time
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webapp.inventory import collector
from webapp.inventory.vm_associations import collect

GP = '/infra/domains/default/groups/'


class Inventory:
    def __init__(self, vms, active_groups):
        self.active = active_groups
        self.edges = {str(i): [(i*4+j) % active_groups for j in range(4)] for i in range(vms)}
        self.calls = Counter()

    def get(self, path, params):
        if path.endswith('/virtual-machine-group-associations'):
            self.calls['association'] += 1
            rows = [{'path':GP+str(i)} for i in self.edges[params['vm_external_id']]]
        else:
            self.calls['probe'] += 1
            i = int(path.split('/groups/')[1].split('/')[0])
            rows = [{'external_id':'sample'}] if i < self.active else []
        return {'results':rows, 'result_count':len(rows)}


def request_case(vms, groups, active):
    definitions = [{'path':GP+str(i), 'expression':[{'resource_type':'Condition','member_type':'VirtualMachine',
        'key':'Tag','operator':'EQUALS','value':'env|prod'}]} for i in range(groups)]
    baseline = Inventory(vms, active)
    expected = [collector.membership(baseline, group, vm_fast=True)[0] for group in definitions]
    client = Inventory(vms, active)
    metadata, stats = collect(client, [{'external_id':str(i)} for i in range(vms)], workers=8)
    positive = {row['path'] for result in metadata['vms'].values() for row in result['items']}
    actual = ['nonempty' if group['path'] in positive else collector.membership(client, group, vm_fast=True)[0]
              for group in definitions]
    assert actual == expected
    assert all(result['status']=='complete' and len(result['items'])==4 for result in metadata['vms'].values())
    return {'vms':vms, 'groups':groups, 'nonempty_groups':active,
            'previous_probe_only_requests':sum(baseline.calls.values()),
            'new_requests':sum(client.calls.values()), 'new_request_breakdown':dict(client.calls),
            'positive_group_probes_avoided':len(positive), 'vm_coverage':'All VMs complete; four actual groups each',
            'cleanup_classifications_equal':True}


def preparation():
    source = (Path(sys.argv[1]).read_text() if len(sys.argv)>1 else
              subprocess.run(['git','show','59d9d96:webapp/inventory/collector.py'],
                             check=True,capture_output=True,text=True).stdout)
    previous = types.ModuleType('previous_collector'); previous.__package__='webapp.inventory'
    exec(source, previous.__dict__)
    vms = [{'path':'/vm/'+str(i),'external_id':str(i),'display_name':'Synthetic '+str(i)} for i in range(250)]
    candidate_groups = {GP+'tag-'+str(i):'Tag candidate '+str(i) for i in range(500)}
    report = {'tags':{'vm_inventory_complete':True, 'virtual_machines':vms, 'objects':[
        {'name':'prod','scope':'env','vms':{vm['path']:vm['display_name'] for vm in vms},'group_conditions':candidate_groups}]},
        'membership_evidence':{'version':1,'method':'vm_associations','vms':{
            vm['path']:{'status':'complete','items':[{'path':GP+str(i),'name':'Actual '+str(i)} for i in range(4)]}
            for vm in vms}}}
    output = {}
    for label, module in [('previous_tag_expansion',previous),('association_projection',collector)]:
        started = time.perf_counter()
        rows = list(module.iter_vm_inventory_rows(report))
        output[label] = {'seconds':round(time.perf_counter()-started,4),
                         'vm_row_json_bytes':len(json.dumps(rows)),
                         'candidate_edges_expanded':sum(len(row['related_groups']) for row in rows)}
        assert all(row['resolved_group_count']==4 for row in rows)
    return {'vms':250,'tag_candidate_groups_per_vm':500, **output}


if __name__ == '__main__':
    print(json.dumps({'scope':'Synthetic membership request count and Python preparation only. Not collection runtime or a production ETA.',
        'distributed_memberships':request_case(1200,4493,4000),
        'highly_shared_memberships':request_case(1200,4493,4),
        'preparation':preparation(),
        'limitations':'The previous probe-only design sampled identities and did not provide complete VM associations. Direct reads can add requests when many VMs share few groups. Existing pacing, statistics, search, persistence, latency, verification sampling and retries affect total collection time; they are not modeled here.'},indent=2))
