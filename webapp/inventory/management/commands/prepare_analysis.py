"""Warm existing snapshots without rebuilding report tables or contacting NSX."""
from uuid import UUID
from django.core.management.base import BaseCommand
from django.db.models import OuterRef, Subquery
from inventory.models import Environment, Snapshot
from inventory.analysis_index import prepare


class Command(BaseCommand):
    help = 'Prepare reusable segmentation data for latest full snapshots; no NSX requests.'

    def add_arguments(self,parser):
        parser.add_argument('--snapshot',type=UUID,help='Prepare this snapshot instead of the latest per environment.')

    def handle(self,**options):
        if options['snapshot']:
            snapshots=Snapshot.objects.filter(pk=options['snapshot'],browsing_status='ready')
        else:
            latest=Snapshot.objects.filter(environment_id=OuterRef('pk'),testing=False,imported=False).order_by('-generated_at','-created_at').values('pk')[:1]
            ids=Environment.objects.annotate(latest=Subquery(latest)).values('latest')
            snapshots=Snapshot.objects.filter(pk__in=ids,browsing_status='ready')
        for snapshot in snapshots.defer('report','html','summary').iterator(chunk_size=10):
            prepare(snapshot)
            self.stdout.write(f'Prepared {snapshot.pk}')
