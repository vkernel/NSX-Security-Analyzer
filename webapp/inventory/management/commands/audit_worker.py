import subprocess
import signal
import sys
import time
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from inventory.services import claim_job, expire_jobs, fail_job


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
        finally:
            signal.signal(signal.SIGTERM, previous)

    def run_worker(self, options):
        while True:
            close_old_connections()
            expire_jobs()
            job = claim_job()
            if job:
                self.stdout.write("Collecting job {}".format(job.pk))
                try:
                    # A separate process bounds the whole audit, including blocked network calls.
                    with subprocess.Popen([sys.executable, str(settings.BASE_DIR / "manage.py"),
                                           "run_audit", str(job.pk)]) as process:
                        try:
                            process.wait(timeout=settings.AUDIT_TIMEOUT)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                            fail_job(job.pk, "Audit timed out or the worker was interrupted. Earlier reports remain available.")
                        except KeyboardInterrupt:
                            process.kill()
                            process.wait()
                            fail_job(job.pk, "The worker was stopped during collection. You can start a new audit.")
                            raise
                except OSError:
                    fail_job(job.pk, "The worker could not start the collector process.")
                fail_job(job.pk, "The collector process stopped before saving a report.")
            if options["once"]:
                return
            if not job:
                time.sleep(2)
