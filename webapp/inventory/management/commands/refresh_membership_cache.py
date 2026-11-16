"""Optional, separate enrichment process. Never part of collection execution."""
import time
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.utils import timezone
from inventory.models import Environment, AuditJob, MembershipLookup
from inventory.current_membership import subject, refresh, namespace, digest, external_id


class Command(BaseCommand):
    help = 'Warm current VM association caches, yielding to queued/running collections. Does not edit snapshots.'

    def add_arguments(self, parser):
        parser.add_argument('--environment', type=int, required=True)
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 1000: raise CommandError('Limit must be between 1 and 1000.')
        env = Environment.objects.get(pk=options['environment'])
        snapshot = env.snapshots.filter(browsing_status='ready', testing=False).defer('report','html','summary').first()
        if not snapshot: raise CommandError('No browsable snapshot is available.')
        completed = 0
        fresh = set(MembershipLookup.objects.filter(environment=env,namespace=namespace(env),kind='vm',status='complete',expires_at__gt=timezone.now()).values_list('key',flat=True))
        rows = snapshot.snapshotrecord_set.filter(view='vms').order_by('ordinal').values_list('ordinal','data__vm_details')
        for ordinal,details in rows.iterator(chunk_size=100):
            if completed >= options['limit']: break
            if AuditJob.objects.filter(environment=env,status__in=['queued','running']).exists():
                self.stdout.write('Collection has priority; enrichment stopped.')
                break
            try:
                if digest(external_id(details)) in fresh: continue
                refresh(snapshot, 'vm', subject(snapshot, 'vm', ordinal))
            except ValidationError as exc:
                if exc.code == 'collection_busy': break
                raise CommandError('Check the environment configuration before warming memberships.') from exc
            except ValueError:
                self.stdout.write(f'VM row {ordinal}: external identity unavailable; skipped.')
            completed += 1
            # Conservative sequential enrichment, never more than two subjects/s.
            time.sleep(.5)
        self.stdout.write(f'Current membership cache: {completed} subjects processed; saved snapshot unchanged.')
