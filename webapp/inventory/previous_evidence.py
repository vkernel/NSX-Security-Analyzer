"""Small prior-run hints and hit-history projection; never reuse live membership."""
import json
from django.db import connection

RULE_FIELDS = ('path', 'rule_id', 'policy_rule_id', 'unique_id', 'created_at',
    'hit_status', 'statistics_checked_at', 'hit_count', 'last_positive_observation',
    'policy_path', 'statistics_fallback_reason', 'statistics_bulk_retry_at', 'statistics_bulk_failures')


def load(environment):
    pk = environment.snapshots.filter(testing=False).values_list('pk', flat=True).first()
    if pk is None:
        return None
    if connection.vendor != 'postgresql':
        report = environment.snapshots.get(pk=pk).report
        return {**{k: report[k] for k in ('manager','testing','generated_at') if k in report},
            'objects': [{k:r[k] for k in ('path','kind','membership','notes') if k in r} for r in report.get('objects', [])],
            'dfw': {'rules': [{k:r[k] for k in RULE_FIELDS if k in r} for r in report.get('dfw', {}).get('rules', [])]}}
    def project(path):
        return """COALESCE((SELECT jsonb_agg(filtered.value ORDER BY r.position)
            FROM jsonb_array_elements(COALESCE(""" + path + """, '[]'::jsonb)) WITH ORDINALITY r(value, position)
            CROSS JOIN LATERAL (SELECT jsonb_object_agg(key,value) AS value
                FROM jsonb_each(r.value) WHERE key = ANY(%s::text[])) filtered), '[]'::jsonb)"""
    sql = "SELECT jsonb_build_object('manager', report->'manager', 'testing', report->'testing', 'generated_at', report->'generated_at', 'objects', " + project("report->'objects'") + ", 'dfw', jsonb_build_object('rules', " + project("report->'dfw'->'rules'") + ')) FROM inventory_snapshot WHERE id = %s'
    with connection.cursor() as cursor:
        cursor.execute(sql, [list(('path','kind','membership','notes')), list(RULE_FIELDS), pk])
        value = cursor.fetchone()[0]
    return json.loads(value) if isinstance(value, str) else value
