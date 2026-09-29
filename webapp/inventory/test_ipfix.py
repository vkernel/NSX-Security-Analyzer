"""Synthetic protocol fixtures only; no captured customer traffic."""
import struct
from django.test import SimpleTestCase
from .ipfix.inspect import inspect_message, InvalidMessage


def message(set_id, body, domain=7):
    payload = struct.pack('!HH', set_id, len(body) + 4) + body
    return struct.pack('!HHIII', 10, len(payload) + 16, 100, 42, domain) + payload


class IPFIXInspectionTests(SimpleTestCase):
    def test_template_preserves_order_duplicates_and_enterprise_fields(self):
        body = struct.pack('!HH', 294, 3)
        body += struct.pack('!HH', 8, 4) * 2
        body += struct.pack('!HHI', 0x8001, 4, 6876)
        result = inspect_message(message(2, body))
        fields = result['sets'][0]['templates'][0]['fields']
        self.assertEqual([f['element_id'] for f in fields], [8, 8, 1])
        self.assertEqual(fields[2]['enterprise_number'], 6876)
        self.assertEqual(result['observation_domain_id'], 7)

    def test_options_scope_and_variable_length(self):
        body = struct.pack('!HHH', 300, 2, 1) + struct.pack('!HHHH', 149, 4, 82, 65535)
        fields = inspect_message(message(3, body))['sets'][0]['templates'][0]['fields']
        self.assertTrue(fields[0]['scope'])
        self.assertFalse(fields[1]['scope'])
        self.assertTrue(fields[1]['variable_length'])

    def test_data_without_template_is_not_guessed(self):
        row = inspect_message(message(294, b'\x00' * 20))['sets'][0]
        self.assertEqual(row, {'id': 294, 'bytes': 24, 'kind': 'data', 'decoded': False})

    def test_invalid_lengths_versions_and_reserved_sets(self):
        valid = message(294, b'1234')
        for data in (b'', valid[:-1], valid + b'0', b'\x00\x09' + valid[2:], message(4, b''),
                     valid[:16] + struct.pack('!HH', 294, 0) + b'1234'):
            with self.subTest(data=data), self.assertRaises(InvalidMessage):
                inspect_message(data)

    def test_malformed_templates_and_udp_withdrawals(self):
        for body in (struct.pack('!HH', 294, 0), struct.pack('!HH', 294, 1025),
                     struct.pack('!HHHH', 294, 1, 0x8001, 4),
                     struct.pack('!HHHH', 294, 1, 8, 0)):
            with self.subTest(body=body), self.assertRaises(InvalidMessage):
                inspect_message(message(2, body))
        with self.assertRaises(InvalidMessage):
            inspect_message(message(3, struct.pack('!HHH', 300, 1, 2)))

    def test_padding_and_multiple_templates(self):
        body = struct.pack('!HHHH', 294, 1, 8, 4) + struct.pack('!HHHH', 295, 1, 27, 16)
        rows = inspect_message(message(2, body + b'\x00\x00'))['sets'][0]['templates']
        self.assertEqual([r['id'] for r in rows], [294, 295])


from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from .models import Environment, IPFIXExporter, IPFIXReceiver
from .ipfix.receiver import Receiver


@override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class IPFIXReceiverTests(TestCase):
    def setUp(self):
        self.environment = Environment.objects.create(slug='pilot', name='Pilot', manager='nsx.example.invalid')
        self.exporter = IPFIXExporter.objects.create(environment=self.environment, address='192.0.2.1')
        IPFIXReceiver.objects.create(pk=1, enabled=True)

    def test_mapped_data_and_templates_are_batched_without_raw_storage(self):
        receiver = Receiver()
        receiver.ingest(message(2, struct.pack('!HHHH', 294, 1, 8, 4)), '192.0.2.1', 4444)
        receiver.ingest(message(294, b'\xc0\x00\x02\x05'), '192.0.2.1', 4444)
        self.exporter.refresh_from_db()
        self.assertEqual(self.exporter.messages, 0)
        receiver.flush()
        self.exporter.refresh_from_db()
        self.assertEqual(self.exporter.messages, 2)
        self.assertEqual(self.exporter.data_sets, 1)
        self.assertEqual(self.exporter.templates[0]['source_port'], 4444)
        self.assertEqual(self.exporter.templates[0]['fields'][0]['element_id'], 8)
        self.assertEqual(IPFIXReceiver.objects.get(pk=1).received, 2)

    def test_unknown_disabled_and_malformed_inputs(self):
        receiver = Receiver()
        receiver.ingest(b'bad', '192.0.2.2', 1)
        receiver.ingest(b'bad', '192.0.2.1', 1)
        receiver.flush()
        self.exporter.refresh_from_db()
        self.assertEqual(self.exporter.malformed, 1)
        self.assertEqual(IPFIXReceiver.objects.get(pk=1).rejected, 1)
        IPFIXReceiver.objects.filter(pk=1).update(enabled=False)
        receiver.refresh()
        receiver.ingest(message(294, b'1234'), '192.0.2.1', 1)
        receiver.flush()
        self.assertEqual(IPFIXReceiver.objects.get(pk=1).rejected, 2)

    def test_previews_bounded_and_separate_domains(self):
        receiver = Receiver()
        for domain in range(20):
            receiver.ingest(message(2, struct.pack('!HHHH',294,1,8,4),domain), '192.0.2.1', 1)
        receiver.flush()
        self.exporter.refresh_from_db()
        self.assertEqual(len(self.exporter.templates), 16)
        self.assertEqual(self.exporter.templates[-1]['domain'], 19)

    def test_duplicate_mapping_cannot_mix_environments(self):
        other=Environment.objects.create(slug='other',name='Other',manager='other.example.invalid')
        with self.assertRaises(IntegrityError), transaction.atomic():
            IPFIXExporter.objects.create(environment=other,address='192.0.2.1')

    def test_staff_permission_form_validation_and_csrf(self):
        user=get_user_model().objects.create_user(username='operator',password='synthetic-test-password')
        self.client.force_login(user)
        self.assertEqual(self.client.get('/administration/ipfix/').status_code,403)
        user.is_staff=True
        user.save()
        self.assertContains(self.client.get('/administration/ipfix/'),'Not decoded')
        response=self.client.post('/administration/ipfix/',{'action':'add','environment':self.environment.pk,'address':'invalid'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(IPFIXExporter.objects.count(),1)
        guarded=Client(enforce_csrf_checks=True)
        guarded.force_login(user)
        self.assertEqual(guarded.post('/administration/ipfix/',{'action':'toggle'}).status_code,403)


class IPFIXDiagnosticsTests(TestCase):
    setUp = IPFIXReceiverTests.setUp

    def test_reasons_expiry_and_bounds_without_payload_storage(self):
        from datetime import timedelta
        from django.utils import timezone
        receiver = Receiver()
        receiver.ingest(b'private-payload', '192.0.2.99', 100)
        receiver.ingest(b'private-payload', '192.0.2.1', 101)
        self.assertEqual([d['reason'] for d in receiver.diagnostics],
                         ['Unmapped or disabled source', 'Malformed or unsupported message'])
        self.assertNotIn('private-payload', str(receiver.diagnostics))
        for port in range(200):
            receiver.ingest(b'xx', '192.0.2.99', port)
        self.assertEqual(len(receiver.diagnostics), 100)
        receiver.diagnostics[0]['last_seen'] = (timezone.now()-timedelta(hours=2)).isoformat()
        receiver.flush(queue_drops=3, socket_drops=2, socket_drop_monitoring=True)
        config = IPFIXReceiver.objects.get(pk=1)
        self.assertEqual(len(config.diagnostics), 99)
        self.assertEqual((config.queue_drops, config.socket_drops), (3, 2))
        receiver.flush()
        self.assertEqual(IPFIXReceiver.objects.get(pk=1).queue_drops, 3)


class IPFIXTransportTests(SimpleTestCase):
    def test_bounded_intake_and_counter_drain(self):
        from unittest.mock import Mock
        from .ipfix.transport import PacketPump
        pump = PacketPump(Mock(), capacity=1)
        pump.enqueue(b'first', ('192.0.2.1', 10))
        pump.enqueue(b'second', ('192.0.2.1', 10))
        self.assertEqual(pump.packets.get_nowait()[:3], (b'first', '192.0.2.1', 10))
        self.assertEqual(pump.counters(), (1, 0))
        self.assertEqual(pump.counters(), (0, 0))

    def test_socket_drop_counter_wrap(self):
        import socket
        from unittest.mock import Mock
        from .ipfix.transport import PacketPump
        pump = PacketPump(Mock())
        pump.last_socket_drops = 2**32-2
        pump.account([(socket.SOL_SOCKET, pump.drop_option, struct.pack('=I', 3))])
        self.assertEqual(pump.counters(), (0, 5))

    def test_udp_thread_receives_while_consumer_is_idle(self):
        import socket
        from .ipfix.transport import PacketPump
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(('127.0.0.1', 0))
            receiver.settimeout(.1)
            pump = PacketPump(receiver)
            pump.start()
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(b'synthetic', receiver.getsockname())
                packet = pump.packets.get(timeout=2)
                self.assertEqual(packet[0], b'synthetic')
                self.assertEqual(packet[1], '127.0.0.1')
                self.assertIsNone(pump.error)
            finally:
                pump.stop()
            self.assertFalse(pump.thread.is_alive())
