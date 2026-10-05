import subprocess
import signal
import sys
import time
from django.core.management.base import CommandError
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, DatabaseError
from inventory.diagnostics import LOG, log_failure, phase
from inventory.services import claim_job, expire_jobs, fail_job, stop_requested, finish_stopped_job


class Command(BaseCommand):
    help = "Process queued NSX audits. Run multiple workers with PostgreSQL for concurrency."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process at most one queued job, then exit.")

    def handle(self, *args, **options):
        def stop(signum, frame):
            raise KeyboardInterrupt()

        previous = signal.signal(signal.SIGTERM, stop)
        try:
            self.run_worker(options)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            log_failure('worker', exc)
            raise CommandError('Worker stopped after an error; inspect structured diagnostics.') from None
        finally:
            signal.signal(signal.SIGTERM, previous)

    def wait_for_collection(self, process, job_id):
        deadline = time.monotonic() + settings.AUDIT_TIMEOUT
        while True:
            if stop_requested(job_id):
                LOG.warning("job=%s stop requested; killing collector and rolling back unfinished work", job_id)
                process.kill()
                process.wait()
                finish_stopped_job(job_id)
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("run_audit", settings.AUDIT_TIMEOUT)
            try:
                process.wait(timeout=min(2, remaining))
                if stop_requested(job_id):
                    finish_stopped_job(job_id)
                return
            except subprocess.TimeoutExpired:
                continue

    def run_worker(self, options):
        LOG.info("Worker started timeout_seconds=%s", settings.AUDIT_TIMEOUT)
        while True:
            close_old_connections()
            with phase('worker', 'expire_stale_jobs', quiet=True):
                expire_jobs()
            with phase('worker', 'claim_queued_job', quiet=True):
                job = claim_job()
            if job and stop_requested(job.pk):
                finish_stopped_job(job.pk)
                if options["once"]:
                    return
                continue
            if job:
                LOG.info("job=%s claimed; starting collector subprocess", job.pk)
                try:
                    # A separate process bounds the whole audit, including blocked network calls.
                    with subprocess.Popen([sys.executable, str(settings.BASE_DIR / "manage.py"),
                                           "run_audit", str(job.pk)]) as process:
                        try:
                            self.wait_for_collection(process, job.pk)
                            LOG.info("job=%s collector exited returncode=%s", job.pk, process.returncode)
                        except subprocess.TimeoutExpired:
                            LOG.error("job=%s collection timed out after %s seconds; terminating subprocess", job.pk, settings.AUDIT_TIMEOUT)
                            process.kill()
                            process.wait()
                            fail_job(job.pk, "Audit timed out or the worker was interrupted. Earlier reports remain available.", {"error": {"code": "COLLECTION_TIMEOUT", "timeout_seconds": settings.AUDIT_TIMEOUT}})
                        except KeyboardInterrupt:
                            LOG.warning("job=%s worker stopping; terminating subprocess", job.pk)
                            process.kill()
                            process.wait()
                            fail_job(job.pk, "The worker was stopped during collection. You can start a new audit.", {"error": {"code": "WORKER_STOPPED"}})
                            raise
                        except DatabaseError:
                            LOG.error("job=%s database supervision failed; killing collector; status reconciliation requires database recovery", job.pk)
                            process.kill()
                            process.wait()
                            raise
                        except Exception:
                            process.kill()
                            process.wait()
                            raise
                except OSError:
                    LOG.error("job=%s collector subprocess could not start", job.pk)
                    fail_job(job.pk, "The worker could not start the collector process.")
                fail_job(job.pk, "The collector process stopped before saving a report.", {"error": {"code": "COLLECTOR_EXIT", "returncode": process.returncode if "process" in locals() else None}})
            if options["once"]:
                return
            if not job:
                time.sleep(2)
