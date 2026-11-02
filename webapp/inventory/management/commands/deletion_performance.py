"""Read-only timing summary; contains no object names or NSX configuration."""
from datetime import timedelta
import json
import math
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from inventory.models import DeletionItem

class Command(BaseCommand):
    help = 'Summarize latest recorded deletion-item timing samples for production comparison.'
    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--json', action='store_true')
    def handle(self, *args, **options):
        if not 1 <= options['days'] <= 365: raise CommandError('Days must be between 1 and 365.')
        rows=DeletionItem.objects.filter(finished_at__gte=timezone.now()-timedelta(days=options['days'])).order_by('-finished_at').values_list('verification', flat=True)[:10001]
        samples={'preview':[], 'execution':[]};shared=0;count=0
        for info in rows.iterator(chunk_size=500):
            count+=1
            if count>10000: break
            timing=info.get('timings',{})
            phase=timing.get('phase')
            if phase not in samples: continue
            samples[phase].append(timing)
            shared+=timing.get('shared_preview_scan') is True
        report={'days':options['days'],'latest_items_limit':10000,'truncated':count>10000,'shared_preview_samples':shared,
                'note':'Latest attempt per object only; excludes queue time. Compare equal workloads before and after deployment.'}
        for phase, timings in samples.items():
            report[phase]={'samples':len(timings)}
            for key in ('total_seconds','dependency_seconds','identity_approval_seconds','condition_reference_seconds'):
                values=sorted(float(t[key]) for t in timings if isinstance(t.get(key),(int,float)))
                report[phase][key]={'p50':values[math.ceil(len(values)*.5)-1], 'p95':values[math.ceil(len(values)*.95)-1]} if values else None
        self.stdout.write(json.dumps(report,indent=2) if options['json'] else '\n'.join(f'{key}: {value}' for key,value in report.items()))
