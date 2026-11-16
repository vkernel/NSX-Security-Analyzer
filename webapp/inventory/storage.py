"""Lossless sharing of immutable search text. Writers require a transaction."""
import hashlib
from django.db import connection, transaction
from .models import SnapshotSearchDocument

LOCK_KEY = 739102851


def share_search(rows):
    if not connection.in_atomic_block:
        raise RuntimeError('Search sharing requires an atomic publication.')
    # Writers can run together. Orphan GC never races an uncommitted reference.
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock_shared(%s)', [LOCK_KEY])
    by_digest = {}
    for row in rows:
        if row.search_document_id or not row.search_evidence:
            continue
        digest = hashlib.sha256(row.search_evidence.encode('utf-8')).hexdigest()
        by_digest.setdefault(digest, []).append(row)
    existing = set(SnapshotSearchDocument.objects.filter(digest__in=by_digest).values_list('digest', flat=True))
    SnapshotSearchDocument.objects.bulk_create([
        SnapshotSearchDocument(digest=digest, text=records[0].search_evidence)
        for digest, records in sorted(by_digest.items()) if digest not in existing], ignore_conflicts=True, batch_size=100)
    documents = {doc.digest: doc for doc in SnapshotSearchDocument.objects.filter(digest__in=by_digest)}
    for digest, records in by_digest.items():
        document = documents[digest]
        for row in records:
            if document.text != row.search_evidence:
                raise ValueError('Search document digest collision; original record retained.')
            row.search_document = document
            row.search_evidence = ''


def clean_search_documents(limit=100):
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_try_advisory_xact_lock(%s)', [LOCK_KEY])
                if not cursor.fetchone()[0]:
                    return 0
        ids = list(SnapshotSearchDocument.objects.filter(snapshotrecord__isnull=True).values_list('pk', flat=True)[:limit])
        removed = SnapshotSearchDocument.objects.filter(pk__in=ids).delete()[0]
        from .models import SharedSnapshotJSON
        ids = list(SharedSnapshotJSON.objects.filter(snapshotrelationship__isnull=True, snapshotvmrule__isnull=True).values_list('pk', flat=True)[:limit])
        return removed + SharedSnapshotJSON.objects.filter(pk__in=ids).delete()[0]


def measure_storage():
    """Daily small metadata sample; no payload scans or remote filesystem claims."""
    from datetime import timedelta
    from django.db.models import Count, Min, Max
    from django.utils import timezone
    from .models import StorageMeasurement, Snapshot
    today = timezone.localdate()
    if connection.vendor != 'postgresql' or StorageMeasurement.objects.filter(day=today).exists():
        return
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = '3000ms'")
        cursor.execute('SELECT pg_database_size(current_database())')
        size = cursor.fetchone()[0]
        rows = list(Snapshot.objects.order_by().values('environment_id', 'environment__name').annotate(
            snapshots=Count('pk'), oldest=Min('generated_at'), newest=Max('generated_at')))
        for row in rows:
            row['oldest'] = row['oldest'].isoformat() if row['oldest'] else None
            row['newest'] = row['newest'].isoformat() if row['newest'] else None
        StorageMeasurement.objects.get_or_create(day=today, defaults={'database_bytes': size, 'environments': rows})
        StorageMeasurement.objects.filter(day__lt=today-timedelta(days=90)).delete()


def storage_trend():
    from .models import StorageMeasurement, RetentionPolicy
    from django.utils import timezone
    rows = list(StorageMeasurement.objects.order_by('-day')[:8])
    if not rows:
        return {}
    latest, oldest = rows[0], rows[-1]
    days = (latest.day-oldest.day).days
    policy = RetentionPolicy.objects.filter(pk=1).first() or RetentionPolicy()
    growth = (latest.database_bytes-oldest.database_bytes)//days if days else None
    budget = policy.database_budget_gib * 1024**3
    stale = (timezone.localdate()-latest.day).days > 2
    forecast = max(0, latest.database_bytes + growth * policy.storage_forecast_days) if growth is not None else None
    percent = round(latest.database_bytes / budget * 100, 1) if budget else None
    alert = ''
    if stale:
        alert = 'Storage measurements are stale. Check the scheduler; forecasts are unavailable.'
    elif budget and percent >= policy.storage_warning_percent:
        alert = f'Application database is at {percent}% of its configured budget.'
    elif budget and forecast is not None and growth > 0 and forecast >= budget:
        alert = f'At the measured growth rate, the database may reach its budget within {policy.storage_forecast_days} days.'
    return {'day': latest.day, 'environments': latest.environments, 'daily_growth': growth,
            'budget': budget, 'percent': percent, 'forecast': None if stale else forecast,
            'horizon': policy.storage_forecast_days, 'alert': alert, 'stale': stale}


def share_json(rows):
    """Share exact JSON objects for paged relationships and VM rule definitions."""
    import json
    from .models import SharedSnapshotJSON
    if not connection.in_atomic_block:
        raise RuntimeError('JSON sharing requires an atomic publication.')
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock_shared(%s)', [LOCK_KEY])
    grouped = {}
    for row in rows:
        if row.payload_id:
            continue
        encoded = json.dumps(row.data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        # Small, unique edge objects cost more as separate rows. Large definitions
        # and repeated structures benefit from sharing across snapshots.
        if len(encoded.encode('utf-8')) < 256:
            continue
        digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        grouped.setdefault(digest, []).append(row)
    existing = set(SharedSnapshotJSON.objects.filter(digest__in=grouped).values_list('digest', flat=True))
    SharedSnapshotJSON.objects.bulk_create([SharedSnapshotJSON(digest=d, data=rs[0].data)
        for d, rs in sorted(grouped.items()) if d not in existing], ignore_conflicts=True, batch_size=100)
    saved = {p.digest:p for p in SharedSnapshotJSON.objects.filter(digest__in=grouped)}
    for digest, records in grouped.items():
        for row in records:
            if row.data != saved[digest].data:
                raise ValueError('JSON digest collision; original evidence retained.')
            row.payload = saved[digest]
            row.data = {}
