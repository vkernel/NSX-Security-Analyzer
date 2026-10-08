"""Independent PostgreSQL leases for collection and finding-recalculation workers."""
from contextlib import contextmanager
from django.db import connection

LANES = {'collection': 781249311, 'recalculation': 781249312}


@contextmanager
def lease(lane):
    # SQLite remains a single-worker development configuration.
    if connection.vendor != 'postgresql':
        yield None
        return
    dedicated = connection.copy(alias='worker_lane')
    try:
        dedicated.ensure_connection()
        with dedicated.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [LANES[lane]])
            acquired = cursor.fetchone()[0]
        yield dedicated if acquired else False
    finally:
        dedicated.close()
