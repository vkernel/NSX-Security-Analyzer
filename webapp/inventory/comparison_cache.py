"""Shared comparison results; report payloads are read only for an explicit uncached pair."""
from django.db import transaction, connection
from .models import Snapshot, SnapshotComparison, SnapshotComparisonRow
from .comparison import compare, COMPARISON_FIELDS
import json
import logging
from time import perf_counter


def configuration(snapshot):
    if connection.vendor == 'postgresql':
        # Strip large membership/counter payloads on the server before transfer.
        # Keep missing keys missing and array order intact, including legacy fallback.
        fields = list(COMPARISON_FIELDS) + ['path', 'kind']
        def project(expression):
            return """COALESCE((SELECT jsonb_agg(filtered.value ORDER BY item.position)
                FROM jsonb_array_elements(COALESCE(""" + expression + """, '[]'::jsonb))
                    WITH ORDINALITY AS item(value, position)
                CROSS JOIN LATERAL (SELECT COALESCE(jsonb_object_agg(key, value), '{}'::jsonb) AS value
                    FROM jsonb_each(item.value) WHERE key = ANY(%s::text[])) filtered), '[]'::jsonb)"""
        inventory = """CASE WHEN report->'inventory' IS NULL THEN NULL ELSE
            '{}'::jsonb ||
            CASE WHEN report->'inventory' ? 'groups' THEN jsonb_build_object('groups', """ + project("report->'inventory'->'groups'") + """) ELSE '{}'::jsonb END ||
            CASE WHEN report->'inventory' ? 'services' THEN jsonb_build_object('services', """ + project("report->'inventory'->'services'") + """) ELSE '{}'::jsonb END END"""
        objects = "CASE WHEN (report->'inventory') ?& ARRAY['groups', 'services'] THEN '[]'::jsonb ELSE " + project("report->'objects'") + ' END'
        sql = 'SELECT ' + inventory + ', ' + objects + ", CASE WHEN report->'dfw'->'rules' IS NULL THEN NULL ELSE " + project("report->'dfw'->'rules'") + " END, report->'dfw'->'errors' FROM inventory_snapshot WHERE id = %s"
        with connection.cursor() as cursor:
            cursor.execute(sql, [fields, fields, fields, fields, snapshot.pk])
            values = cursor.fetchone()
        data = dict(zip(('report__inventory','report__objects','report__dfw__rules','report__dfw__errors'),
            (json.loads(v) if isinstance(v, str) else v for v in values)))
    else:
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
                started = perf_counter()
                left, right = configuration(before), configuration(after)
                loaded = perf_counter()
                rows = compare(left, right)
                compared = perf_counter()
                for offset in range(0, len(rows), 100):
                    SnapshotComparisonRow.objects.bulk_create([
                        SnapshotComparisonRow(comparison=saved, ordinal=offset+i, data=row)
                        for i, row in enumerate(rows[offset:offset+100])])
                saved.ready = True
                saved.save(update_fields=['ready'])
                logging.getLogger('inventory.collection').info(
                    'Comparison pair=%s projection_seconds=%.3f compare_seconds=%.3f persist_seconds=%.3f changes=%d',
                    saved.pk, loaded-started, compared-loaded, perf_counter()-compared, len(rows))
    return saved.rows.values_list('data', flat=True)
