import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections, OperationalError
from inventory.diagnostics import LOG, phase, log_failure
from inventory.audit_events import cleanup
from inventory.services import schedule_due
from inventory.retention import cleanup_retention


class Command(BaseCommand):
    help = "Queue due automatic environment syncs from persisted database schedules."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        LOG.info("Scheduler started poll_seconds=15")
        while True:
            close_old_connections()
            try:
                with phase('scheduler', 'schedule_due', quiet=True):
                    count = schedule_due()
                with phase('scheduler', 'retention_cleanup', quiet=True):
                    cleanup_retention()
                with phase('scheduler', 'audit_retention', quiet=True):
                    cleanup()
                if count:
                    LOG.info("Scheduler queued collections count=%s", count)
            except OperationalError as exc:
                log_failure("scheduler", exc)
                if options["once"]:
                    raise
                LOG.warning("Scheduler database unavailable; retrying in 15 seconds")
            if options["once"]:
                return
            time.sleep(15)
