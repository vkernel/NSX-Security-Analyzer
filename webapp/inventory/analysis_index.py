"""Small snapshot projections, prepared once and shared across processes.

No NSX requests. Rebuilding/archiving a snapshot discards these derived rows.
Components are separate columns so grouping settings never load firewall rules.
"""
import logging
from time import perf_counter
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Snapshot, SnapshotRecord, SnapshotAnalysisIndex, SnapshotNamingAssessment, SnapshotVMMembership

VERSION = 1
RULE_LIMIT = 10000
LOG = logging.getLogger('inventory.web')


def invalidate(snapshot_id):
    SnapshotAnalysisIndex.objects.filter(snapshot_id=snapshot_id).delete()
    SnapshotNamingAssessment.objects.filter(snapshot_id=snapshot_id).delete()
    SnapshotVMMembership.objects.filter(snapshot_id=snapshot_id).delete()


@receiver(post_save, sender=SnapshotRecord)
def record_changed(sender, instance, raw=False, update_fields=None, **kwargs):
    # Publication uses bulk_create and explicitly prepares the projection once.
    if not raw and (update_fields is None or {'data','name','view','ordinal','snapshot','snapshot_id'} & set(update_fields)):
        invalidate(instance.snapshot_id)


@receiver(post_save, sender=Snapshot)
def snapshot_changed(sender, instance, created=False, raw=False, update_fields=None, **kwargs):
    if not created and not raw and (update_fields is None or {'report', 'browsing_status', 'browsing_refreshed_at'} & set(update_fields)):
        invalidate(instance.pk)


def read_groups(snapshot):
    from .naming import group_evidence
    rows = SnapshotRecord.objects.filter(snapshot=snapshot, data__kind='group').order_by('ordinal').values(
        'pk', 'name', 'data__path', 'data__configuration', 'data__membership', 'data__unique_id')
    return [dict(id=r['pk'], name=r['name'], path=r['data__path'], definition=r['data__configuration'],
                 unique_id=r['data__unique_id'], membership=r['data__membership'] or 'unknown',
                 evidence=group_evidence(r['data__configuration'])) for r in rows.iterator(chunk_size=500) if r['data__path']]


def read_rules(snapshot):
    # Project only configuration and labels, never unrelated references/history.
    fields = ('path', 'name', 'configuration', 'policy_name', 'policy_path', 'category', 'action', 'disabled', 'hit_count')
    rows = SnapshotRecord.objects.filter(snapshot=snapshot, data__configuration__rule__isnull=False).order_by('ordinal').values(
        'name', *('data__'+field for field in fields))[:RULE_LIMIT+1]
    from .naming import digest
    result = []
    for row in rows:
        item = {field:row['data__'+field] for field in fields if row['data__'+field] is not None}
        item['name'] = item.get('name') or row['name']
        item['configuration'] = item.get('configuration') or {}
        item['fingerprint'] = digest(item['configuration'])
        result.append(item)
    return result


def read_services(snapshot):
    rows = SnapshotRecord.objects.filter(snapshot=snapshot, data__kind__in=['service', 'custom_service']).values('name', 'data__path', 'data__configuration')
    return {r['data__path']:{'name':r['name'], 'definition':r['data__configuration']} for r in rows if r['data__path']}


def load(snapshot, *components):
    """Return fresh dicts from the DB; callers may safely attach request URLs/reviews."""
    if snapshot.browsing_status != 'ready':
        return {name:[] if name != 'services' else {} for name in components}
    started = perf_counter()
    fields = ['version', *components]
    row = SnapshotAnalysisIndex.objects.filter(snapshot=snapshot).values(*fields).first()
    hit = bool(row and row['version'] == VERSION and all(row[name] is not None for name in components))
    if not hit:
        SnapshotAnalysisIndex.objects.get_or_create(snapshot=snapshot)
        with transaction.atomic():
            saved = SnapshotAnalysisIndex.objects.select_for_update().get(snapshot=snapshot)
            if saved.version != VERSION:
                saved.application_maps.all().delete()
                SnapshotVMMembership.objects.filter(snapshot=snapshot).delete()
                SnapshotNamingAssessment.objects.filter(snapshot=snapshot).delete()
                saved.groups = saved.rules = saved.services = None
                saved.version = VERSION
            for name in components:
                if getattr(saved, name) is None:
                    setattr(saved, name, {'groups':read_groups, 'rules':read_rules, 'services':read_services}[name](snapshot))
            saved.save()
            row = {name:getattr(saved, name) for name in components}
    LOG.info('Snapshot analysis projection', extra={'details':{'snapshot':str(snapshot.pk), 'cache_hit':hit, 'components':list(components), 'duration_seconds':round(perf_counter()-started, 4)}})
    return {name:row[name] for name in components}


def prepare(snapshot):
    result=load(snapshot, 'groups', 'rules', 'services')
    from .models import SegmentationSettings
    from .application_maps import cached_map
    scope=SegmentationSettings.objects.filter(environment_id=snapshot.environment_id).values_list('grouping_scope',flat=True).first() or ''
    cached_map(snapshot,scope=scope)
    return result
