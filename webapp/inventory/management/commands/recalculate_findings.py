from django.core.management.base import BaseCommand
from inventory.finding_recalculation import run, fail
from inventory.diagnostics import log_failure


class Command(BaseCommand):
    help = 'Run a claimed finding-history recalculation (invoked by audit_worker).'

    def add_arguments(self, parser):
        parser.add_argument('job_id', type=int)
        parser.add_argument('token')

    def handle(self, *args, **options):
        try:
            run(options['job_id'], options['token'])
        except Exception as exc:
            log_failure('finding_recalculation', exc)
            fail(options['job_id'], options['token'])
