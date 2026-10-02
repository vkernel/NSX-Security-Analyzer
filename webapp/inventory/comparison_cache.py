"""Shared comparison results; report payloads are read only for an explicit uncached pair."""
from django.db import transaction
from .models import Snapshot, SnapshotComparison, SnapshotComparisonRow
from .comparison import compare


def configuration(snapshot):
    data = Snapshot.objects.filter(pk=snapshot.pk).values(
        'report__inventory', 'report__objects', 'report__dfw__rules', 'report__dfw__errors').get()
    result = {'objects': data['report__objects'] or [],
              'dfw': {'rules': data['report__dfw__rules'] or [], 'errors': data['report__dfw__errors'] or []}}
    if data['report__dfw__rules'] is None and data['report__dfw__errors'] is None:
        result.pop('dfw')
    # Missing inventory must remain missing, so presence changes retain uncertainty.
    if data['report__inventory'] is not None:
        result['inventory'] = data['report__inventory']
    return result


def comparison_rows(before, after):
    saved, _ = SnapshotComparison.objects.get_or_create(before=before, after=after)
    if not saved.ready:
        with transaction.atomic():
            saved = SnapshotComparison.objects.select_for_update().get(pk=saved.pk)
            if not saved.ready:
                rows = compare(configuration(before), configuration(after))
                for offset in range(0, len(rows), 100):
                    SnapshotComparisonRow.objects.bulk_create([
                        SnapshotComparisonRow(comparison=saved, ordinal=offset+i, data=row)
                        for i, row in enumerate(rows[offset:offset+100])])
                saved.ready = True
                saved.save(update_fields=['ready'])
    return saved.rows.values_list('data', flat=True)
