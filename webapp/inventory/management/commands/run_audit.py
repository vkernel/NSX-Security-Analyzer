from django.core.management.base import BaseCommand
from inventory.services import execute_job


class Command(BaseCommand):
    help = "Execute one already claimed job (used by audit_worker)."

    def add_arguments(self, parser):
        parser.add_argument("job_id")

    def handle(self, *args, **options):
        execute_job(options["job_id"])
