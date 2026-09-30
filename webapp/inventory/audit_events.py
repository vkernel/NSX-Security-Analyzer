import logging
from django.db import transaction
from .observability import CONTEXT, safe_data

LOG = logging.getLogger('inventory.audit')


def record(action, target_type='', target_id='', outcome='success', details=None, actor=None, best_effort=False):
    from .models import AuditEvent
    context = CONTEXT.get()
    values = dict(action=action, target_type=target_type, target_id=str(target_id or ''), outcome=outcome,
                  actor_id_text=str(actor if actor is not None else context.get('actor_id', '')),
                  request_id=context.get('request_id', ''), details=safe_data({'peer_address': context.get('peer_address', ''), **(details or {})}))
    try:
        # Savepoint prevents a failed audit insert from poisoning an outer transaction.
        with transaction.atomic():
            event = AuditEvent.objects.create(**values)
        transaction.on_commit(lambda: LOG.info('audit event committed', extra={'details': {'event_id': str(event.pk), **values}}))
        return event
    except Exception as exc:
        LOG.error('audit persistence failed', extra={'details': {'code': 'AUDIT_PERSISTENCE_FAILED',
                  'exception': type(exc).__name__, 'audit_event': values}})
        if not best_effort:
            raise


def cleanup():
    from datetime import timedelta
    from django.conf import settings
    from django.utils import timezone
    from .models import AuditEvent
    days = settings.AUDIT_EVENT_RETENTION_DAYS
    if not days:
        return
    with transaction.atomic():
        ids = list(AuditEvent.objects.filter(created_at__lt=timezone.now()-timedelta(days=days)).values_list('pk', flat=True)[:1000])
        if ids:
            count, _ = AuditEvent.objects.filter(pk__in=ids).delete()
            record('audit.retention', details={'retention_days': days, 'deleted_events': count})
