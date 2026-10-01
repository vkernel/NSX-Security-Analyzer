import gc
import time
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from inventory.models import Snapshot, SnapshotPresentation, SnapshotPanel, SnapshotRecord
from inventory.snapshot_index import build


class Command(BaseCommand):
    help = 'Prepare saved snapshots for fast report pages; no NSX requests are made.'

    def add_arguments(self, parser):
        parser.add_argument('--refresh', action='store_true', help='Rebuild prepared report layouts and tables as well as missing indexes.')

    def handle(self, **options):
        snapshots = Snapshot.objects.all()
        if not options['refresh']:
            snapshots = snapshots.filter(Q(presentation__isnull=True) | Q(history_data__isnull=True))
        ids = snapshots.values_list('pk', flat=True)
        for pk in ids.iterator(chunk_size=100):
            snapshot = Snapshot.objects.defer("html", "summary").filter(pk=pk).first()
            if snapshot is None: continue
            started = time.monotonic()
            self.stdout.write("Indexing snapshot " + str(pk))
            self.stdout.flush()
            with transaction.atomic():
                Snapshot.objects.select_for_update().only('pk').get(pk=pk)
                if options['refresh']:
                    SnapshotPresentation.objects.filter(snapshot_id=pk).delete()
                    SnapshotPanel.objects.filter(snapshot_id=pk).delete()
                    SnapshotRecord.objects.filter(snapshot_id=pk).delete()
                build(snapshot)
            self.stdout.write('Indexed snapshot {} in {:.1f}s'.format(pk, time.monotonic()-started))
            del snapshot
            gc.collect()
        self.stdout.write(self.style.SUCCESS('Snapshot indexes are up to date.'))
