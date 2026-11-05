import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from .collector import MembershipProbe, membership, AuditError, NSXClient
from .resolved_membership import collect, endpoint_plan
from .member_collection_budget import DetailBudget, DetailBudgetExceeded, request_deadline
from .request_pacing import RequestPacer, CollectionDeadlineExceeded
from .concurrency import AdaptiveRequests, request_category

VM_GROUP = {'path': '/infra/domains/default/groups/g', 'expression': [
    {'resource_type': 'Condition', 'member_type': 'VirtualMachine', 'key': 'Tag', 'operator': 'EQUALS', 'value': 'env|prod'}]}


class MemberBudgetTests(SimpleTestCase):
    def test_shared_initial_page_is_read_once_and_typed_categories_are_pruned(self):
        client = SimpleNamespace(get=Mock(side_effect=lambda path, params: {
            'results': [{'id':str(i)} for i in range(10)] if path.endswith('virtual-machines') else [],
            'result_count':10 if path.endswith('virtual-machines') else 0}))
        budget = DetailBudget().group(VM_GROUP['path'])
        probe = MembershipProbe(client, detail_budget=budget)
        self.assertEqual(membership(probe, VM_GROUP)[0], 'nonempty')
        result = collect(client, VM_GROUP, probe=probe, budget=budget)
        self.assertEqual(result['endpoints']['virtual-machines']['status'], 'complete')
        self.assertEqual(client.get.call_count, 3)
        paths = [call.args[0] for call in client.get.call_args_list]
        self.assertEqual(sum(path.endswith('virtual-machines') for path in paths), 1)
        self.assertFalse(any(path.endswith('logical-switches') for path in paths))
        self.assertEqual(budget.owner.summary()['endpoints']['virtual-machines']['reused_pages'], 1)

    def test_probe_continuation_resumes_without_restarting_page_one(self):
        client = SimpleNamespace(get=Mock(return_value={'results':[{'id':'b'}], 'result_count':2}))
        probe = MembershipProbe(client)
        probe.pages['virtual-machines'] = {'results':[{'id':'a'}], 'result_count':2, 'cursor':'opaque'}
        result = collect(client, VM_GROUP, probe=probe)
        self.assertEqual(result['endpoints']['virtual-machines']['status'], 'complete')
        self.assertEqual(client.get.call_args_list[0].args[1]['cursor'], 'opaque')
        self.assertEqual([row['id'] for row in result['endpoints']['virtual-machines']['items']], ['a','b'])

    def test_pure_ip_definitions_require_no_reads_and_are_labelled_configured(self):
        group = {'path':'/g', 'expression':[{'resource_type':'IPAddressExpression', 'ip_addresses':['192.0.2.0/24']}]}
        client = SimpleNamespace(get=Mock())
        result = collect(client, group)
        client.get.assert_not_called()
        self.assertEqual(result['endpoints']['ip-addresses']['status'], 'configured')
        self.assertEqual(result['endpoints']['ip-addresses']['items'][0]['identity'], '192.0.2.0/24')
        self.assertEqual(result['endpoints']['virtual-machines']['status'], 'not_applicable')
        self.assertIsNone(endpoint_plan(dict(group, expression=[{
            'resource_type':'IPAddressExpression', 'ip_addresses':[123]}]))[1])
        for expressions in ([{'resource_type':'PathExpression','paths':['/child']}],
                             [VM_GROUP['expression'][0], {'resource_type':'ConjunctionOperator','conjunction_operator':'OR'}, {'resource_type':'Condition','member_type':'VIF'}], [None]):
            self.assertEqual(len(endpoint_plan(dict(group, expression=expressions))[0]), 4)

    def test_disabled_details_keep_mandatory_checks_and_partial_positive_evidence(self):
        client = SimpleNamespace(get=Mock(return_value={'results':[{'id':'a'}], 'result_count':2, 'cursor':'next'}))
        budget = DetailBudget(requests=0).group(VM_GROUP['path'])
        probe = MembershipProbe(client, detail_budget=budget)
        self.assertEqual(membership(probe, VM_GROUP)[0], 'nonempty')
        result = collect(client, VM_GROUP, probe=probe, budget=budget)
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(client.get.call_args.args[1], {'page_size':1})
        self.assertEqual(result['endpoints']['virtual-machines']['status'], 'partial')
        self.assertEqual(len(result['endpoints']['virtual-machines']['items']), 1)
        self.assertEqual(result['endpoints']['ip-addresses']['status'], 'not_collected')

    def test_optional_timeout_falls_back_to_fresh_core_probe_and_restores_context(self):
        now = [0.0]
        owner = DetailBudget(clock=lambda:now[0], group_seconds=1)
        client = SimpleNamespace(request_context=threading.local())
        def get(path, params):
            if params['page_size']>1:
                now[0] += 2
                raise AuditError('optional read timeout')
            self.assertFalse(hasattr(client.request_context, 'member_detail'))
            self.assertFalse(hasattr(client.request_context, 'phase_deadline'))
            return {'results':[{'id':'a'}], 'result_count':2, 'cursor':'next'}
        client.get = Mock(side_effect=get)
        budget = owner.group(VM_GROUP['path']); probe = MembershipProbe(client, detail_budget=budget)
        self.assertEqual(membership(probe, VM_GROUP)[0], 'nonempty')
        result = collect(client, VM_GROUP, probe=probe, budget=budget)
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(result['endpoints']['virtual-machines']['status'], 'partial')
        self.assertEqual(owner.summary()['requests'], 1)

    def test_global_and_per_group_request_caps_are_atomic(self):
        owner = DetailBudget(requests=2)
        client = SimpleNamespace(get=Mock(return_value={'results':[], 'result_count':0}), request_context=threading.local())
        def work():
            try: owner.group('/g').fetch(client, '/g/members/virtual-machines', {'page_size':1000})
            except DetailBudgetExceeded: pass
        threads = [threading.Thread(target=work) for _ in range(20)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(client.get.call_count, 2); self.assertEqual(owner.summary()['requests'], 2)
        group = DetailBudget(group_requests=1).group('/g')
        group.fetch(client, '/g/members/virtual-machines', {'page_size':1000})
        with self.assertRaises(DetailBudgetExceeded): group.fetch(client, '/g/members/ip-addresses', {'page_size':1000})

    def test_global_time_and_group_time_caps_preserve_mandatory_empty_semantics(self):
        now = [0.0]
        owner = DetailBudget(seconds=1, group_seconds=10, clock=lambda:now[0])
        group = owner.group('/g'); now[0]=2
        client = SimpleNamespace(get=Mock(return_value={'results':[], 'result_count':0}))
        probe = MembershipProbe(client, detail_budget=group)
        self.assertEqual(membership(probe, VM_GROUP, vm_fast=True)[0], 'empty')
        self.assertEqual(client.get.call_count, 1)
        result = collect(client, VM_GROUP, probe=probe, budget=group)
        self.assertEqual(result['endpoints']['virtual-machines']['status'], 'complete')
        self.assertEqual(client.get.call_count, 1)
        owner = DetailBudget(seconds=10, group_seconds=1, clock=lambda:now[0]); group=owner.group('/g'); now[0]+=2
        with self.assertRaises(DetailBudgetExceeded): group.fetch(client, '/g/members/ip-addresses', {'page_size':1000})

    def test_optional_reads_do_not_retry_and_core_retry_policy_is_preserved(self):
        import time
        client=NSXClient.__new__(NSXClient); client.retries=2; client.request_context=threading.local()
        client._get=Mock(side_effect=AuditError('busy',503))
        with request_deadline(client, time.monotonic()+10), patch('inventory.collector.time.sleep'):
            with self.assertRaises(AuditError): client.get('/g/members/virtual-machines', {'page_size':1000})
        self.assertEqual(client._get.call_count, 1)
        client._get.reset_mock()
        with patch('inventory.collector.time.sleep'):
            with self.assertRaises(AuditError): client.get('/g/members/virtual-machines', {'page_size':1})
        self.assertEqual(client._get.call_count, 3)

    def test_collection_deadline_is_fatal_and_phase_deadline_is_optional(self):
        now = [10.0]; pacer=RequestPacer(clock=lambda:now[0], deadline=20)
        with self.assertRaises(TimeoutError) as error: pacer.acquire(deadline=9)
        self.assertNotIsInstance(error.exception, CollectionDeadlineExceeded)
        now[0]=21
        with self.assertRaises(CollectionDeadlineExceeded): pacer.acquire(deadline=9)
        client=SimpleNamespace(get=Mock(side_effect=CollectionDeadlineExceeded('fatal')))
        probe=MembershipProbe(client, detail_budget=DetailBudget().group('/g'))
        with self.assertRaises(CollectionDeadlineExceeded): membership(probe, VM_GROUP)
        self.assertEqual(client.get.call_count, 1)
        with self.assertRaises(CollectionDeadlineExceeded): collect(client, VM_GROUP)

    def test_probe_and_page_latency_baselines_are_separate_but_429_still_reduces_limit(self):
        now=[0.0]; control=AdaptiveRequests(maximum=8, clock=lambda:now[0]); control.limit=8
        path='/g/members/virtual-machines'
        for size, duration in [(1,.05)]*5+[(1000,3.0)]*8:
            category=request_category(path, {'page_size':size})
            start=control.acquire(endpoint=category); now[0]+=duration; control.release(start, endpoint=category)
        self.assertEqual(control.limit, 8)
        self.assertEqual(len(control.summary()['endpoints']), 2)
        category=request_category(path, {'page_size':1000})
        start=control.acquire(endpoint=category); control.release(start, AuditError('busy',429), endpoint=category)
        self.assertEqual(control.limit, 4)

    def test_live_diagnostics_include_details_and_current_concurrency(self):
        now=[0.0]; control=AdaptiveRequests(clock=lambda:now[0]); start=control.acquire(endpoint='membership/virtual-machines/page'); now[0]=31
        with self.assertLogs('inventory.collector', level='INFO') as logs: control.release(start, endpoint='membership/virtual-machines/page')
        self.assertTrue(any('limit=' in line and 'p95_seconds' in line for line in logs.output))
        now[0]=0; budget=DetailBudget(clock=lambda:now[0]); client=SimpleNamespace(get=Mock(return_value={'results':[]}))
        now[0]=31
        with self.assertLogs('nsx_inventory', level='INFO') as logs: budget.group('/g').fetch(client, '/g/members/virtual-machines', {'page_size':1000})
        self.assertTrue(any('Member detail progress:' in line for line in logs.output))
