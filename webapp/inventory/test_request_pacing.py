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
        self.assertEqual(pacer.rate,5.5)
        self.assertEqual(pacer.summary()['throttled_attempts'],2)

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
