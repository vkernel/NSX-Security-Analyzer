from django.core.management.base import BaseCommand
from inventory.models import SnapshotComparison
from inventory.comparison_cache import comparison_rows

class Command(BaseCommand):
    help = 'Prepare a queued comparison outside the web process.'
    def add_arguments(self, parser):
        parser.add_argument('comparison_id', type=int)
    def handle(self, *args, **options):
        pair = SnapshotComparison.objects.select_related('before', 'after').defer('before__report', 'after__report').get(pk=options['comparison_id'])
        comparison_rows(pair.before, pair.after)
