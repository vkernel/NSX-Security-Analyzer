from django.core.management.base import BaseCommand, CommandError
from inventory.diagnostics import log_failure
from inventory.services import execute_job


class Command(BaseCommand):
    help = "Execute one already claimed job (used by audit_worker)."

    def add_arguments(self, parser):
        parser.add_argument("job_id")

    def handle(self, *args, **options):
        try:
            execute_job(options["job_id"])
        except Exception as exc:
            log_failure(options['job_id'], exc)
            raise CommandError('Collector failed; inspect structured diagnostics.') from None
