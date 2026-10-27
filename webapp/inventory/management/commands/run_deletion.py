from django.core.management.base import BaseCommand
from inventory.deletion import run


class Command(BaseCommand):
    help = 'Process one claimed NSX deletion preview or confirmed batch.'

    def add_arguments(self, parser):
        parser.add_argument('batch_id')

    def handle(self, *args, **options):
        run(options['batch_id'])
