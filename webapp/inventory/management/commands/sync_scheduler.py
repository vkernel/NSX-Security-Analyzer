import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections, OperationalError
from inventory.services import schedule_due
from inventory.retention import cleanup_retention


class Command(BaseCommand):
    help = "Queue due automatic environment syncs from persisted database schedules."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                count = schedule_due()
                cleanup_retention()
                if count:
                    self.stdout.write(f"Queued {count} scheduled sync(s)")
            except OperationalError:
                if options["once"]:
                    raise
                self.stderr.write("Schedule database unavailable; retrying shortly.")
            if options["once"]:
                return
            time.sleep(15)
