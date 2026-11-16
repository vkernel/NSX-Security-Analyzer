from django.core.management.base import BaseCommand
from inventory.reference_exports import prepare


class Command(BaseCommand):
    help = 'Prepare an already claimed reference export without contacting NSX.'

    def add_arguments(self, parser):
        parser.add_argument('export_id')

    def handle(self, *args, **options):
        prepare(options['export_id'])
