"""Internal optional Compose receiver; configuration is managed in the GUI."""
import queue
import signal
import socket
import time
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from inventory.ipfix.receiver import Receiver
from inventory.ipfix.transport import PacketPump


class Command(BaseCommand):
    help = 'Run the experimental metadata-only IPFIX UDP receiver'

    def add_arguments(self, parser):
        parser.add_argument('--bind', default='0.0.0.0')
        parser.add_argument('--port', type=int, default=2055)

    def handle(self, *args, **options):
        if not 1 <= options['port'] <= 65535:
            raise CommandError('Port must be 1–65535')
        if connection.vendor != 'postgresql':
            raise CommandError('The receiver requires PostgreSQL')
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(20557011)')
            if not cursor.fetchone()[0]:
                raise CommandError('An IPFIX receiver is already running for this database')
        running = True
        def stop(*_):
            nonlocal running
            running = False
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        health = Path('/tmp/ipfix-heartbeat')
        try:
            receiver = Receiver()
            family = socket.AF_INET6 if ':' in options['bind'] else socket.AF_INET
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
                sock.bind((options['bind'], options['port']))
                sock.settimeout(1)
                pump = PacketPump(sock)
                pump.start()
                last_flush = time.monotonic()
                self.stdout.write('IPFIX receiver started; bounded intake and source diagnostics enabled.')
                try:
                    while running:
                        if pump.error:
                            raise CommandError('IPFIX socket intake failed; receiver will restart.') from pump.error
                        try:
                            receiver.ingest(*pump.packets.get(timeout=1))
                        except queue.Empty:
                            pass
                        if time.monotonic() - last_flush >= 5:
                            receiver.flush(*pump.counters(), socket_drop_monitoring=pump.monitoring)
                            health.touch()
                            last_flush = time.monotonic()
                finally:
                    pump.stop()
                # Intake stopped; drain the bounded queue before final persistence.
                while not pump.packets.empty():
                    receiver.ingest(*pump.packets.get_nowait())
                receiver.flush(*pump.counters(), socket_drop_monitoring=pump.monitoring)

        finally:
            health.unlink(missing_ok=True)
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(20557011)')
