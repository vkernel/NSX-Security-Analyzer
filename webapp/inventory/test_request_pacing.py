import threading
import time
from types import SimpleNamespace
from unittest.mock import patch, Mock
from django.test import SimpleTestCase, TestCase
from .request_pacing import RequestPacer
from .collector import AuditError, NSXClient, parse_retry_after
from .models import Environment, AuditJob
from .services import claim_job


class PacingTests(SimpleTestCase):
    def test_throttle_cooldown_and_gradual_recovery(self):
        now = [100.0]
        pacer = RequestPacer(clock=lambda: now[0])
        pacer.result(AuditError('busy',429,15))
        self.assertEqual(pacer.rate,5)
        self.assertGreaterEqual(pacer.cooldown,115)
        pacer.result(AuditError('busy',429))
        self.assertEqual(pacer.rate,5)
        now[0]=131
        pacer.result()
        self.assertEqual(pacer.rate,5)
        now[0] = 146
        for _ in range(20): pacer.result()
        self.assertEqual(pacer.rate,6.25)
        self.assertEqual(pacer.summary()['throttled_attempts'],2)

    def test_rate_grows_above_start_and_stops_at_ceiling(self):
        now=[0.0]
        pacer=RequestPacer(clock=lambda:now[0])
        for _ in range(20):
            now[0]+=10
            for _ in range(20): pacer.result()
        self.assertEqual(pacer.rate,40)
        with self.assertLogs('inventory.collector',level='INFO') as logs:
            now[0]+=30
            pacer.result()
        self.assertFalse(any('increased' in line for line in logs.output))
        self.assertTrue(any('completed=' in line for line in logs.output))

    def test_concurrent_requests_are_spaced(self):
        pacer=RequestPacer()
        times=[]
        def work():
            pacer.acquire()
            times.append(time.monotonic())
        threads=[threading.Thread(target=work) for _ in range(4)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(2)
        self.assertEqual(len(times),4)
        self.assertTrue(all(b-a>=.09 for a,b in zip(sorted(times),sorted(times)[1:])))

    def test_deadline_prevents_long_server_cooldown(self):
        pacer=RequestPacer(deadline=time.monotonic()+1)
        pacer.result(AuditError('busy',429,120))
        with self.assertRaises(TimeoutError):pacer.acquire()

    def test_retry_after_formats_and_budget(self):
        self.assertEqual(parse_retry_after('15'),15)
        with patch('inventory.collector.time.time',return_value=0):
            self.assertEqual(parse_retry_after('Thu, 01 Jan 1970 00:00:30 GMT'),30)
        for value in ('nonsense','nan','-1'):self.assertIsNone(parse_retry_after(value))
        client=NSXClient.__new__(NSXClient)
        client.retries=2
        client._get=Mock(side_effect=AuditError('busy',429,120))
        with patch('inventory.collector.time.sleep') as sleep:
            with self.assertRaises(AuditError):client.get('/test')
            sleep.assert_not_called()
        self.assertEqual(client._get.call_count,1)


class ManagerClaimTests(TestCase):
    def test_duplicate_origin_waits_other_manager_can_run(self):
        env=Environment.objects.create(name='One',slug='one',manager='https://nsx.example')
        running=AuditJob.objects.create(environment=env,status='running',config={'manager':'https://NSX.example:443/'})
        duplicate=Environment.objects.create(name='Two',slug='two',manager='https://NSX.example:443/')
        different=Environment.objects.create(name='Three',slug='three',manager='https://other.example')
        blocked=AuditJob.objects.create(environment=duplicate,config={'manager':'nsx.example'})
        other=AuditJob.objects.create(environment=different,config={'manager':'https://other.example'})
        self.assertEqual(claim_job().pk,other.pk)
        self.assertIsNone(claim_job())
        running.status='succeeded';running.save()
        self.assertEqual(claim_job().pk,blocked.pk)


class MembershipOrderingTests(SimpleTestCase):
    def test_positive_vm_evidence_requires_one_call(self):
        from .collector import membership
        client=SimpleNamespace(get=Mock(return_value={'results':[{'id':'vm'}]}))
        group={'path':'/group','expression':[{'resource_type':'Condition','member_type':'VirtualMachine'}]}
        self.assertEqual(membership(client,group)[0],'nonempty')
        self.assertEqual(client.get.call_count,1)
        self.assertTrue(client.get.call_args.args[0].endswith('/virtual-machines'))

    def test_old_hint_is_rechecked_and_empty_checks_are_not_skipped(self):
        from .collector import membership, MEMBERSHIP_ENDPOINTS
        client=SimpleNamespace(membership_hints={'/group':'logical-ports'},get=Mock(return_value={'results':[]}))
        group={'path':'/group','expression':[{'resource_type':'Condition','member_type':'VirtualMachine'}]}
        self.assertEqual(membership(client,group)[0],'empty')
        self.assertTrue(client.get.call_args_list[0].args[0].endswith('/logical-ports'))
        self.assertEqual({call.args[0].rsplit('/',1)[1] for call in client.get.call_args_list},set(MEMBERSHIP_ENDPOINTS))

    def test_failed_probe_is_unknown_not_empty(self):
        from .collector import membership
        client=SimpleNamespace(get=Mock(side_effect=[AuditError('throttled',429),{'results':[]},{'results':[]},{'results':[]}]))
        self.assertEqual(membership(client,{'path':'/group','expression':[]})[0],'unknown')


class QueuedBudgetTests(SimpleTestCase):
    def test_long_queue_wait_does_not_exhaust_retry_budget(self):
        from .concurrency import adapt_requests
        now = [0.0]
        client = NSXClient.__new__(NSXClient)
        client.retries = 1
        client.request_deadline = 1000
        client._get = Mock(side_effect=[AuditError('busy', 503), {'results': []}])
        with patch('inventory.collector.time.monotonic', side_effect=lambda: now[0]):
            adapt_requests(client)
            def wait_for_slot(*args): now[0] += 70
            with patch.object(client.request_pacer, 'acquire', side_effect=wait_for_slot), patch('inventory.collector.time.sleep'):
                self.assertEqual(client.get('/test'), {'results': []})
                self.assertGreaterEqual(client.request_context.deadline, 200)

    def test_shared_retry_after_is_not_paid_twice(self):
        from .concurrency import adapt_requests
        client = NSXClient.__new__(NSXClient)
        client.retries = 1
        client._get = Mock(side_effect=[AuditError('busy', 429, 120), {'results': []}])
        adapt_requests(client)
        with patch.object(client.request_pacer, 'acquire'), patch('inventory.collector.time.sleep') as sleep:
            self.assertEqual(client.get('/test'), {'results': []})
            sleep.assert_not_called()
        self.assertEqual(client.request_pacer.throttles, 1)

    def test_whole_collection_deadline_is_not_membership_unknown(self):
        from .concurrency import adapt_requests
        from .collector import membership
        from .request_pacing import CollectionDeadlineExceeded
        client = NSXClient.__new__(NSXClient)
        client.retries = 1
        client.request_deadline = time.monotonic() - 1
        client._get = Mock(return_value={'results': []})
        adapt_requests(client)
        with self.assertRaises(CollectionDeadlineExceeded):
            membership(client, {'path': '/group', 'expression': []})
        self.assertEqual(client.request_pacer.completed, 1)
