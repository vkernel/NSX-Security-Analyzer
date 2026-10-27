"""Persist bounded, payload-free NSX access diagnostics on the owning operation."""
import logging
from .audit_events import record


def failures(client):
    rows = getattr(client, 'access_failures', None)
    return rows[:100] if isinstance(rows, list) else []


def audit_failures(client, environment_id, *, item=None, job=None):
    rows = failures(client)
    for row in rows:
        details = {**row, 'environment_id': environment_id}
        if item:
            details.update(batch=str(item.batch_id), ticket=item.batch.ticket, name=item.name)
        if job:
            details['collection_job'] = str(job.pk)
        record('nsx.access_denied', 'DeletionItem' if item else 'AuditJob', item.pk if item else job.pk,
               outcome='failed', details=details, actor=item.batch.requested_by_id if item else '', best_effort=True)
    return rows


def save_collection_access(client, job):
    from .models import AuditJob
    rows = audit_failures(client, job.environment_id, job=job)
    if rows:
        try:
            diagnostics = AuditJob.objects.filter(pk=job.pk).values_list('diagnostics', flat=True).first() or {}
            diagnostics['access_failures'] = rows
            AuditJob.objects.filter(pk=job.pk).update(diagnostics=diagnostics)
        except Exception:
            logging.getLogger(__name__).exception('Could not persist NSX access diagnostics for collection %s', job.pk)
