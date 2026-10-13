"""Independent PostgreSQL leases for collection, finding-review and comparison workers."""
from contextlib import contextmanager
from django.db import connection

# Session leases and transaction claim locks share PostgreSQL's advisory-lock
# namespace. 310 is the collection claim lock; 313 is the recalculation claim
# lock. Never reuse either for a worker's lifetime lease.
LANES = {'collection': 781249311, 'recalculation': 781249312, 'comparison': 781249314}


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
