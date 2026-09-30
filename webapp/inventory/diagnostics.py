"""Bounded console diagnostics: identifiers and timings, never payloads or secrets."""
import logging
import threading
import time
from datetime import datetime, timezone
from contextlib import contextmanager

LOG = logging.getLogger("inventory.collection")


TIMELINE = []


def log_failure(job_id, exc):
    from .observability import exception_details
    detail = exception_details(exc)
    LOG.error("job=%s failed exception=%s", job_id, type(exc).__name__, extra={'details': {'error': detail}})
    return detail


@contextmanager
def phase(job_id, name, interval=30, quiet=False):
    started = time.monotonic()
    stopped = threading.Event()
    LOG.log(logging.DEBUG if quiet else logging.INFO, "job=%s phase=%s started", job_id, name)

    def heartbeat():
        while not stopped.wait(interval):
            LOG.info("job=%s phase=%s still_running elapsed_seconds=%.1f",
                     job_id, name, time.monotonic() - started)

    stamp = datetime.now(timezone.utc).isoformat()
    row = {'stage': name, 'started_at': stamp, 'outcome': 'running'}
    if len(TIMELINE) < 100:
        TIMELINE.append(row)
    thread = threading.Thread(target=heartbeat, name="collection-diagnostics", daemon=True)
    thread.start()
    try:
        yield
    except BaseException:
        row["outcome"] = "interrupted"
        LOG.warning("job=%s phase=%s interrupted elapsed_seconds=%.1f",
                    job_id, name, time.monotonic() - started)
        raise
    else:
        row["outcome"] = "finished"
        LOG.log(logging.DEBUG if quiet else logging.INFO, "job=%s phase=%s finished elapsed_seconds=%.1f",
                 job_id, name, time.monotonic() - started)
    finally:
        row["duration_seconds"] = round(time.monotonic() - started, 3)
        stopped.set()
        thread.join()
