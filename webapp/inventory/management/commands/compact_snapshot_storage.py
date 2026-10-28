"""Explicit bounded maintenance; stopping between batches is safe."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, connection
from django.db.models import Q
import json
from inventory.models import SnapshotRecord
from inventory.storage import share_search


class Command(BaseCommand):
    help = 'Share exact duplicate search text without rebuilding snapshots. Defaults to read-only sample.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--batch-size', type=int, default=25)
        parser.add_argument('--max-batches', type=int, default=1)
        parser.add_argument('--after-id', type=int, default=0)

    def handle(self, **options):
        size, batches = options['batch_size'], options['max_batches']
        if not 1 <= size <= 100 or not 1 <= batches <= 1000:
            raise CommandError('Batch size must be 1–100 and max batches 1–1000.')
        cursor = options['after_id']
        for _ in range(batches):
            with transaction.atomic():
                if connection.vendor == "postgresql":
                    with connection.cursor() as sql:
                        sql.execute("SET LOCAL statement_timeout = '30s'")
                        sql.execute("SET LOCAL lock_timeout = '1s'")
                query = SnapshotRecord.objects.filter(pk__gt=cursor).filter(Q(search_document__isnull=True) & ~Q(search_evidence='') | Q(data__has_key='resolved_tag_evidence')).order_by('pk')
                if options['apply']:
                    query = query.select_for_update()
                rows = list(query.only('pk', 'search_evidence', 'search_document', 'data', 'view', 'snapshot_id')[:size])
                if not rows:
                    break
                original = sum(len(row.search_evidence.encode('utf-8')) for row in rows)
                tag_bytes = sum(len(json.dumps(row.data.get('resolved_tag_evidence', {})).encode('utf-8')) for row in rows if 'resolved_tag_evidence' in row.data)
                unique = sum(len(value.encode('utf-8')) for value in {row.search_evidence for row in rows})
                if options['apply']:
                    from inventory.tag_evidence import for_tag
                    for row in rows:
                        if row.view == 'tags' and 'resolved_tag_evidence' in row.data:
                            expected = for_tag(row.snapshot_id, row.data)
                            if json.loads(json.dumps(expected)) != row.data['resolved_tag_evidence']:
                                raise CommandError('Shared tag evidence differs; batch rolled back.')
                            row.data = dict(row.data)
                            del row.data['resolved_tag_evidence']
                            row.data['_shared_tag_evidence'] = True
                    share_search(rows)
                    SnapshotRecord.objects.bulk_update(rows, ['search_document', 'search_evidence', 'data'], batch_size=size)
                cursor = rows[-1].pk
            self.stdout.write(f'{"Applied" if options["apply"] else "Dry run"}: {len(rows)} records; logical text bytes={original}; unique within batch={unique}; embedded tag bytes={tag_bytes}; resume --after-id {cursor}')
            self.stdout.flush()
        self.stdout.write('Finished bounded run. Re-run to resume; converted rows are skipped. Byte counts are not filesystem savings.')
