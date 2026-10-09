import time
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    help = 'Read the local supervisor heartbeat; does not contact the database.'
    def add_arguments(self, parser):
        parser.add_argument('services', nargs='+', choices=['collection', 'recalculation', 'scheduler'])
    def handle(self, *args, **options):
        for name in options['services']:
            try: age = time.time() - Path('/tmp/nsxa-heartbeat-' + name).stat().st_mtime
            except OSError: age = 181
            if age > 180: raise CommandError(f'{name} has not reported within 180 seconds')
