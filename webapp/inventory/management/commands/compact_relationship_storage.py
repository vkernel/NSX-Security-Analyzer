from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, connection
from inventory.models import SnapshotRelationship, SnapshotVMRule
from inventory.storage import share_json
import json


class Command(BaseCommand):
    help = 'Share large relationship or VM-rule JSON. Defaults to dry run; source observations are untouched.'

    def add_arguments(self, parser):
        parser.add_argument('--kind', choices=['relationships', 'vm-rules'], default='relationships')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--after-id', type=int, default=0)
        parser.add_argument('--batch-size', type=int, default=25)
        parser.add_argument('--max-batches', type=int, default=1)

    def handle(self, **options):
        if not 1 <= options['batch_size'] <= 100 or not 1 <= options['max_batches'] <= 1000:
            raise CommandError('Use batch-size 1–100 and max-batches 1–1000.')
        model = SnapshotRelationship if options['kind'] == 'relationships' else SnapshotVMRule
        cursor = options['after_id']
        for _ in range(options['max_batches']):
            with transaction.atomic():
                if connection.vendor == 'postgresql':
                    with connection.cursor() as sql:
                        sql.execute("SET LOCAL statement_timeout = '30s'")
                        sql.execute("SET LOCAL lock_timeout = '1s'")
                query = model.objects.filter(pk__gt=cursor, payload__isnull=True).order_by('pk')
                if options['apply']: query = query.select_for_update()
                rows = list(query.only('pk', 'payload', 'data')[:options['batch_size']])
                if not rows: break
                size = sum(len(json.dumps(row.data).encode('utf-8')) for row in rows)
                if options['apply']:
                    share_json(rows)
                    model.objects.bulk_update(rows, ['payload', 'data'], batch_size=options['batch_size'])
                cursor = rows[-1].pk
            self.stdout.write(f'{"Applied" if options["apply"] else "Dry run"}: {len(rows)} {options["kind"]}; logical bytes={size}; resume --after-id {cursor}')
            self.stdout.flush()
        self.stdout.write('Bounded run finished. Resume with the printed checkpoint; small inline JSON is intentionally retained.')
