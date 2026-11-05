"""Offline request-volume and latency model; no NSX, database, or credentials.

Run from a Git checkout: python benchmarks/membership_collection.py
The previous implementation is read from the published 8196992 commit.
Timing numbers are modeled lower bounds, not production runtime predictions.
"""
import heapq
import json
from pathlib import Path
import subprocess
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webapp.inventory.collector import MembershipProbe, membership
from webapp.inventory.resolved_membership import collect
from webapp.inventory.member_collection_budget import DetailBudget

GROUPS = 10861
WORKERS = 8
RATE = 40


def definition(index):
    kind = 'ip' if index % 20 < 10 else 'empty' if index % 20 < 16 else 'small' if index % 20 < 19 else 'large'
    expression = ({'resource_type':'IPAddressExpression','ip_addresses':['192.0.2.1']} if kind == 'ip' else
        {'resource_type':'Condition','member_type':'VirtualMachine','key':'Tag','operator':'EQUALS','value':'env|prod'})
    return {'path':f'/infra/domains/default/groups/synthetic-{index}', 'expression':[expression]}, kind


class SyntheticNSX:
    def __init__(self, kind):
        self.kind = kind
        self.trace = []

    def get(self, path, params):
        endpoint = path.rsplit('/',1)[-1]
        total = (10 if self.kind == 'small' else 2500 if self.kind == 'large' else 0) if endpoint == 'virtual-machines' else (1 if self.kind == 'ip' and endpoint == 'ip-addresses' else 0)
        size = params.get('page_size',1000); offset=int(params.get('cursor',0))
        count = min(size, max(0,total-offset))
        latency = .15 if size == 1 else .45 + count*.00005
        self.trace.append(latency)
        values = ['192.0.2.1'] if self.kind == 'ip' and endpoint == 'ip-addresses' else [{'id':f'vm-{offset+i}'} for i in range(count)]
        page = {'results':values,'result_count':total}
        if offset+count < total: page['cursor'] = str(offset+count)
        return page


def estimate(traces):
    # Discrete-event model: each worker issues one request at a time, with a
    # shared burst-free 40-rps admission limit and FIFO group assignment.
    tasks=iter(enumerate(traces)); events=[]; next_request=0.0; finished=0.0
    def assign(ready, worker):
        for task, trace in tasks:
            if trace:
                heapq.heappush(events,(ready,worker,task,0))
                break
    for worker in range(WORKERS):assign(0.0,worker)
    while events:
        ready,worker,task,index=heapq.heappop(events)
        start=max(ready,next_request); next_request=start+1/RATE
        done=start+traces[task][index]; finished=max(finished,done)
        if index+1<len(traces[task]):heapq.heappush(events,(done,worker,task,index+1))
        else:assign(done,worker)
    return round(finished,2)


def run():
    source=subprocess.run(['git','show','8196992:webapp/inventory/resolved_membership.py'],check=True,capture_output=True,text=True).stdout
    previous=types.ModuleType('previous_resolved_membership'); previous.__package__='webapp.inventory';exec(source,previous.__dict__)
    output={}
    for mode in ('baseline_checks','published_8196992','optimized','fast_collection'):
        traces=[]; requests=0; outcomes=[]
        # Hold the budget clock fixed to isolate its strict 2,000-request cap.
        # Time cutoff behavior is tested independently with an advancing clock.
        budget=DetailBudget(clock=lambda:0.0)
        for i in range(GROUPS):
            group,kind=definition(i); client=SyntheticNSX(kind)
            group_budget=budget.group(group['path'])
            probe=MembershipProbe(client,detail_budget=group_budget if mode=='optimized' else None)
            result=membership(probe,group,vm_fast=True); outcomes.append(result[0])
            if mode=='published_8196992':previous.collect(client,group,probe=probe)
            elif mode=='optimized':collect(client,group,probe=probe,budget=group_budget)
            elif mode=='fast_collection':collect(client,group,probe=probe,budget=DetailBudget(seconds=0,requests=0).group(group['path']))
            traces.append(client.trace); requests+=len(client.trace)
        output[mode]={'requests':requests,'modeled_membership_seconds':estimate(traces)}
        if mode=='baseline_checks':baseline=outcomes
        else:assert outcomes==baseline, 'Mandatory membership outcomes changed'
        if mode=='optimized':
            assert requests <= output['baseline_checks']['requests'] + 2000
            output[mode]['detail_requests']=budget.summary()['requests']
    assert output['fast_collection']['requests']==output['baseline_checks']['requests'], 'Browsing enrichment added collection requests'
    output['fast_collection']['additional_collection_requests']=0
    output['assumptions']={'groups':GROUPS,'workers':WORKERS,'max_requests_per_second':RATE,
        'mix':'50% explicit IP, 30% empty VM, 15% 10-member VM, 5% 2500-member VM',
        'latency_seconds':{'probe':.15,'page':'.45 + .00005 per returned member'},
        'budget':'2000 optional requests; time cap separately tested',
        'scope':'Membership branch only; excludes inventory, counters, tags, indexing, transport errors and NSX backpressure. Not a production ETA.'}
    print(json.dumps(output,indent=2))


if __name__=='__main__':run()
