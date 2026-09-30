from django.core.management.base import BaseCommand
from inventory.models import Snapshot
from inventory.snapshot_index import build


class Command(BaseCommand):
    help = 'Prepare saved snapshots for fast report pages; no NSX requests are made.'

    def handle(self, **options):
        ids = Snapshot.objects.filter(presentation__isnull=True).values_list('pk', flat=True)
        for pk in ids.iterator(chunk_size=100):
            snapshot = Snapshot.objects.filter(pk=pk).first()
            if snapshot is None: continue
            build(snapshot)
            self.stdout.write('Indexed snapshot '+str(pk))
        self.stdout.write(self.style.SUCCESS('Snapshot indexes are up to date.'))
