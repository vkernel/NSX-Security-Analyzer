"""Bounded UDP intake, independent of parsing and database latency."""
import queue
import socket
import struct
import sys
import threading
from datetime import datetime, timezone


class PacketPump:
    def __init__(self, sock, capacity=1024):
        self.sock = sock
        self.packets = queue.Queue(maxsize=capacity)
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.queue_drops = self.socket_drops = self.last_socket_drops = 0
        self.error = None
        self.monitoring = False
        # Linux SO_RXQ_OVFL reports cumulative socket receive drops as uint32.
        self.drop_option = getattr(socket, 'SO_RXQ_OVFL', 40)
        if sys.platform.startswith('linux'):
            try:
                sock.setsockopt(socket.SOL_SOCKET, self.drop_option, 1)
                self.monitoring = True
            except OSError:
                pass
        self.thread = threading.Thread(target=self.run, name='ipfix-udp', daemon=True)

    def enqueue(self, payload, peer):
        try:
            self.packets.put_nowait((payload, peer[0], peer[1], datetime.now(timezone.utc)))
        except queue.Full:
            with self.lock:
                self.queue_drops += 1

    def account(self, ancillary):
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == self.drop_option and len(data) >= 4:
                total = struct.unpack('=I', data[:4])[0]
                with self.lock:
                    self.socket_drops += (total - self.last_socket_drops) % (2**32)
                    self.last_socket_drops = total

    def run(self):
        try:
            while not self.stopped.is_set():
                try:
                    payload, ancillary, flags, peer = self.sock.recvmsg(65535, socket.CMSG_SPACE(4))
                except socket.timeout:
                    continue
                self.account(ancillary)
                self.enqueue(payload, peer)
        except Exception as exc:
            self.error = exc
            self.stopped.set()

    def counters(self):
        with self.lock:
            result = self.queue_drops, self.socket_drops
            self.queue_drops = self.socket_drops = 0
            return result

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=2)
