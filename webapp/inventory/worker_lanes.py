"""Independent PostgreSQL leases for collection, finding-review and comparison workers."""
from contextlib import contextmanager
from django.db import connection

# Session leases and transaction claim locks share PostgreSQL's advisory-lock
# namespace. 310 is the collection claim lock; 313 is the recalculation claim
# lock. Never reuse either for a worker's lifetime lease.
LANES = {'collection': 781249311, 'recalculation': 781249312, 'comparison': 781249314, 'deletion_preview': 781249315, 'deletion_execution': 781249316, 'exports': 781249318,
         # Extra collection slots (NSX_COLLECTION_SLOTS); each runs one environment's collection.
         'collection_2': 781249331, 'collection_3': 781249332, 'collection_4': 781249333}
COLLECTION_LANES = ('collection', 'collection_2', 'collection_3', 'collection_4')


def collection_lane(lane):
    return lane in COLLECTION_LANES


def active_lanes():
    """Lanes the supervisor starts: every task lane plus the configured collection slots."""
    from django.conf import settings
    enabled = set(COLLECTION_LANES[:settings.COLLECTION_SLOTS])
    return [lane for lane in LANES if not collection_lane(lane) or lane in enabled]


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
