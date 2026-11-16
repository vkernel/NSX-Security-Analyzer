"""Bounded, read-only observation of concurrent PostgreSQL migration work."""
import logging
import threading
import time
from contextlib import suppress

logger = logging.getLogger('inventory.migrations')
POLL_INTERVAL_SECONDS = 15


class IndexProgress:
    """Observe another backend without sharing its connection or transaction."""

    def __init__(self, connection, index, table):
        self.connection = connection
        self.index = index
        self.table = table
        self.stage = 'checking'
        self.stopped = threading.Event()
        self.started_at = time.monotonic()
        self.previous = None
        self.phase = None
        self.phase_started_at = self.started_at

    def emit(self, event, *, level=logging.INFO, **details):
        logger.log(level, 'Migration 0063 index %s: %s', self.index, event,
                   extra={'details': {
                       'migration': 'inventory.0063_snapshot_projection_indexes',
                       'index': self.index, 'table': self.table,
                       'database_pid': self.backend_pid, 'stage': self.stage,
                       'elapsed_seconds': round(time.monotonic() - self.started_at, 1),
                       'event': event, **details,
                   }})

    def __enter__(self):
        self.connection.ensure_connection()
        self.backend_pid = self.connection.connection.info.backend_pid
        self.emit('started', poll_interval_seconds=POLL_INTERVAL_SECONDS)
        self.thread = threading.Thread(target=self.run, name='migration-index-progress', daemon=True)
        try:
            self.thread.start()
        except RuntimeError:
            self.thread = None
            self.emit('observer_unavailable', level=logging.WARNING, error_type='ThreadStartError')
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.stopped.set()
        # Diagnostics must not hold up completion if their connection is slow.
        if self.thread is not None:
            self.thread.join(timeout=1)
        cause = (exc.__cause__ or exc) if exc else None
        self.emit('failed' if exc else 'finished',
                  level=logging.ERROR if exc else logging.INFO,
                  error_type=exc_type.__name__ if exc_type else None,
                  sqlstate=getattr(cause, 'sqlstate', None))

    def observer_connection(self):
        # Construct and use the wrapper in this thread. The migration connection
        # is never used concurrently, and observer SELECTs hold no long snapshot.
        observer = self.connection.copy(alias='migration-index-observer')
        observer.settings_dict['AUTOCOMMIT'] = True
        observer.settings_dict['CONN_MAX_AGE'] = 0
        options = observer.settings_dict['OPTIONS'].copy()
        options['connect_timeout'] = min(3, max(1, int(options.get('connect_timeout', 3))))
        options['application_name'] = 'nsxa-migration-progress'
        options['options'] = (options.get('options', '') +
                              ' -c default_transaction_read_only=on'
                              ' -c statement_timeout=2000 -c lock_timeout=1000')
        observer.settings_dict['OPTIONS'] = options
        return observer

    @staticmethod
    def row(cursor):
        value = cursor.fetchone()
        return dict(zip([column[0] for column in cursor.description], value)) if value else None

    def input_size(self, observer):
        with observer.cursor() as cursor:
            cursor.execute('''
                SELECT CASE WHEN reltuples >= 0 THEN reltuples::bigint END AS estimated_rows,
                       pg_relation_size(oid) AS heap_bytes,
                       pg_total_relation_size(oid) AS total_bytes
                FROM pg_class WHERE oid = to_regclass(%s)
            ''', [self.table])
            details = self.row(cursor)
        if details and not self.stopped.is_set():
            self.emit('input_size', **details)

    def sample(self, observer):
        with observer.cursor() as cursor:
            cursor.execute('''
                SELECT a.state AS database_state, a.wait_event_type, a.wait_event,
                       CASE WHEN a.state = 'active' THEN
                           EXTRACT(EPOCH FROM clock_timestamp() - a.query_start)::float8
                       END AS query_elapsed_seconds,
                       pg_blocking_pids(a.pid) AS blocking_pids,
                       p.command, p.phase, p.current_locker_pid,
                       p.lockers_done, p.lockers_total,
                       p.blocks_done, p.blocks_total, p.tuples_done, p.tuples_total
                FROM pg_stat_activity a
                LEFT JOIN pg_stat_progress_create_index p ON p.pid = a.pid
                WHERE a.pid = %s AND a.datname = current_database()
            ''', [self.backend_pid])
            details = self.row(cursor)
            if details is None:
                if not self.stopped.is_set():
                    self.emit('backend_not_visible', level=logging.WARNING)
                return
            details['activity_visible'] = details['database_state'] is not None
            blocking = details['blocking_pids'] or []
            details['blocking_pid_count'] = len(blocking)
            details['blocking_pids'] = blocking[:10]
            blocker_ids = list(dict.fromkeys(blocking + [details['current_locker_pid'] or 0]))[:10]
            blocker_ids = [pid for pid in blocker_ids if pid]
            details['blockers'] = []
            if blocker_ids:
                cursor.execute('''
                    SELECT pid, state, wait_event_type, wait_event,
                           EXTRACT(EPOCH FROM clock_timestamp() - xact_start)::float8 AS transaction_age_seconds
                    FROM pg_stat_activity WHERE pid = ANY(%s) ORDER BY pid LIMIT 10
                ''', [blocker_ids])
                columns = [column[0] for column in cursor.description]
                details['blockers'] = [dict(zip(columns, row)) for row in cursor.fetchall()]
        phase = (self.stage, details['phase'])
        if phase != self.phase:
            self.phase, self.phase_started_at = phase, time.monotonic()
        details['phase_observed_seconds'] = round(time.monotonic() - self.phase_started_at, 1)
        progress = (phase, details['blocks_done'], details['tuples_done'], details['lockers_done'])
        details['progress_changed'] = None if self.previous is None else progress != self.previous
        self.previous = progress
        # Zero/unknown counters are retained as reported. Do not invent a whole-
        # migration percentage or ETA from a phase-specific counter.
        if not self.stopped.is_set():
            self.emit('progress', **details)

    def run(self):
        observer = None
        input_logged = False
        try:
            while not self.stopped.is_set():
                try:
                    if observer is None:
                        observer = self.observer_connection()
                    # Log activity even when a relation-size lookup times out.
                    self.sample(observer)
                    if not input_logged and not self.stopped.is_set():
                        input_logged = True
                        self.input_size(observer)
                except Exception as error:
                    # Database errors may contain connection details or SQL.
                    # Only expose the type and SQLSTATE; migration DDL continues.
                    if not self.stopped.is_set():
                        cause = error.__cause__ or error
                        self.emit('observer_unavailable', level=logging.WARNING,
                                  error_type=type(error).__name__,
                                  sqlstate=getattr(cause, 'sqlstate', None))
                    if observer is not None:
                        with suppress(Exception):
                            observer.close()
                        observer = None
                if self.stopped.wait(POLL_INTERVAL_SECONDS):
                    break
        finally:
            if observer is not None:
                with suppress(Exception):
                    observer.close()
