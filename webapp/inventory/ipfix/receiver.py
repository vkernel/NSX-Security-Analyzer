"""Bounded metadata-only receiver processing; one instance per deployment."""
from datetime import timedelta
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from ..models import IPFIXExporter, IPFIXReceiver
from .inspect import inspect_message, InvalidMessage


class Receiver:
    def __init__(self):
        self.pending = {}
        self.accepted = self.rejected = 0
        self.refresh()
        self.diagnostics = list(self.config.diagnostics)

    def refresh(self):
        self.config, _ = IPFIXReceiver.objects.get_or_create(pk=1)
        # Explicit cap prevents unbounded in-memory exporter state.
        self.exporters = {e.address: e for e in IPFIXExporter.objects.filter(enabled=True).order_by('pk')[:1000]}

    def observe(self, address, port, version, reason, stamp):
        cutoff = (timezone.now() - timedelta(hours=1)).isoformat()
        self.diagnostics = [d for d in self.diagnostics if d['last_seen'] > cutoff]
        key = (address, port, version, reason)
        row = next((d for d in self.diagnostics if
            (d['address'], d['port'], d['version'], d['reason']) == key), None)
        if row:
            self.diagnostics.remove(row)
            row['count'] += 1
            row['last_seen'] = stamp.isoformat()
        else:
            row = dict(address=address, port=port, version=version, reason=reason,
                       count=1, last_seen=stamp.isoformat())
        self.diagnostics.append(row)
        self.diagnostics = self.diagnostics[-100:]

    def ingest(self, payload, address, port, arrived=None):
        stamp = arrived or timezone.now()
        version = int.from_bytes(payload[:2], 'big') if len(payload) >= 2 else None
        exporter = self.exporters.get(address)
        if not self.config.enabled or exporter is None:
            self.rejected += 1
            self.observe(address, port, version,
                         'Reception disabled' if not self.config.enabled else 'Unmapped or disabled source', stamp)
            return
        self.accepted += 1
        row = self.pending.setdefault(exporter.pk, {'messages': 0, 'malformed': 0, 'data_sets': 0,
            'templates': list(exporter.templates)[-16:]})
        row['messages'] += 1
        row['last_received'] = stamp
        try:
            message = inspect_message(payload)
        except InvalidMessage:
            row['malformed'] += 1
            self.observe(address, port, version, 'Malformed or unsupported message', stamp)
            return
        self.observe(address, port, version, 'Accepted IPFIX message', stamp)
        for item in message['sets']:
            if item['kind'] == 'data':
                row['data_sets'] += 1
            for template in item.get('templates', []):
                preview = {'domain': message['observation_domain_id'], 'source_port': port,
                    'id': template['id'], 'field_count': len(template['fields']),
                    'fields': template['fields'][:128], 'truncated': len(template['fields']) > 128}
                row['templates'] = [t for t in row['templates'] if
                    (t['domain'], t['source_port'], t['id']) != (preview['domain'], port, preview['id'])]
                row['templates'].append(preview)
                row['templates'] = row['templates'][-16:]

    @transaction.atomic
    def flush(self, queue_drops=0, socket_drops=0, socket_drop_monitoring=False):
        for pk, row in self.pending.items():
            IPFIXExporter.objects.filter(pk=pk).update(
                messages=F('messages') + row['messages'], malformed=F('malformed') + row['malformed'],
                data_sets=F('data_sets') + row['data_sets'], last_received=row['last_received'],
                templates=row['templates'])
        cutoff = (timezone.now() - timedelta(hours=1)).isoformat()
        self.diagnostics = [d for d in self.diagnostics if d['last_seen'] > cutoff]
        IPFIXReceiver.objects.filter(pk=1).update(heartbeat=timezone.now(),
            diagnostics=self.diagnostics,
            queue_drops=F('queue_drops') + queue_drops,
            socket_drops=F('socket_drops') + socket_drops,
            socket_drop_monitoring=socket_drop_monitoring,
            received=F('received') + self.accepted, rejected=F('rejected') + self.rejected)
        self.pending.clear()
        self.accepted = self.rejected = 0
        self.refresh()
