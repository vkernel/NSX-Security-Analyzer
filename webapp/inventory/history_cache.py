"""Shared, invalidated history assessments with database-side paging."""
from datetime import timedelta
from django.apps import apps
from django.db import transaction
from django.db.models import F
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import Environment, AuditJob, Snapshot, HistoryRevision, HistoryAssessment, HistoryAssessmentRow
from .history import analyze


class HistoryChanged(Exception):
    pass


def invalidate(environment_id):
    revisions = HistoryRevision.objects.filter(environment_id=environment_id)
    if not revisions.update(revision=F('revision')+1):
        HistoryRevision.objects.get_or_create(environment_id=environment_id)
        revisions.update(revision=F('revision')+1)


@receiver(post_save, sender=Snapshot)
@receiver(post_delete, sender=Snapshot)
@receiver(post_save, sender=AuditJob)
@receiver(post_delete, sender=AuditJob)
def source_changed(sender, instance, raw=False, **kwargs):
    origin = kwargs.get('origin')
    if isinstance(origin, Environment) or getattr(origin, 'model', None) is Environment:
        return
    if not raw and sender._meta.apps is apps:
        if sender is Snapshot and kwargs.get('signal') is post_save and not kwargs.get('created'):
            if kwargs.get('update_fields') is None or 'report' in kwargs['update_fields']:
                from .models import SnapshotComparison, SnapshotHistoryData
                from django.db.models import Q
                SnapshotComparison.objects.filter(Q(before_id=instance.pk) | Q(after_id=instance.pk)).delete()
                SnapshotHistoryData.objects.filter(snapshot=instance).delete()
        invalidate(instance.environment_id)


def timestamps(data, keys):
    result = dict(data)
    for key in keys:
        if isinstance(result.get(key), str): result[key] = parse_datetime(result[key])
    return result


def assessment(environment, days, end, anchor=None):
    """Rolling assessments are explicitly as-of a time and reused for five minutes."""
    key = str(days)+':'+(str(anchor.pk) if anchor else 'latest')
    HistoryRevision.objects.get_or_create(environment=environment)
    HistoryAssessment.objects.get_or_create(environment=environment, cache_key=key, defaults={'anchor':anchor})
    with transaction.atomic():
        saved = HistoryAssessment.objects.select_for_update().get(environment=environment, cache_key=key)
        revision = HistoryRevision.objects.get(environment=environment).revision
        if saved.revision == revision and saved.expires_at and saved.expires_at > timezone.now():
            return saved
        for attempt in range(2):
            revision = HistoryRevision.objects.get(environment=environment).revision
            result = analyze(environment, days, end)
            if HistoryRevision.objects.get(environment=environment).revision == revision:
                break
        else:
            raise HistoryChanged('Collection history changed during analysis. Please refresh to try again.')
        rows = result.pop('rows')
        saved.rows.all().delete()
        # Keep DB statements and transient model allocations bounded.
        for offset in range(0, len(rows), 250):
            HistoryAssessmentRow.objects.bulk_create([
                HistoryAssessmentRow(assessment=saved, name=r['name'],sort_name=r['name'].casefold(),
                    path=r['path'],rule_id_text=str(r['rule_id']),status=r['status'],data=r)
                for r in rows[offset:offset+250]])
        saved.metadata = result
        saved.revision = revision
        saved.expires_at = timezone.now()+timedelta(minutes=5)
        saved.save(update_fields=['metadata','revision','expires_at'])
    # Old historical anchor caches are disposable, and never grow indefinitely.
    HistoryAssessment.objects.filter(environment=environment, expires_at__lt=timezone.now()-timedelta(days=1)).exclude(pk=saved.pk).delete()
    return saved
