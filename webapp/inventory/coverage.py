"""Collection visibility, not a claim of continuous traffic observation."""
from datetime import timedelta
from django.utils import timezone
from .usability import freshness, policy
from .models import Snapshot, SnapshotRecord, SnapshotPresentation
from django.db.models import Q


def dashboard(environment, days, now=None):
    now = now or timezone.now()
    start = now - timedelta(days=days)
    snapshots = environment.snapshots.filter(testing=False, imported=False)
    stamps = list(snapshots.filter(generated_at__gte=start, generated_at__lte=now).order_by('generated_at').values_list('generated_at', flat=True))
    threshold = timedelta(minutes=environment.sync_interval_minutes * 2) if environment.sync_interval_minutes else timedelta(hours=24)
    boundaries = [start] + stamps + [now]
    gaps = [{'start': a, 'end': b, 'hours': round((b-a).total_seconds()/3600, 2)}
            for a, b in zip(boundaries, boundaries[1:]) if b-a > threshold]
    latest = snapshots.filter(generated_at__lte=now).only('id','generated_at','needs_review').first()
    issues = []
    if latest:
        if SnapshotPresentation.objects.filter(snapshot_id=latest.pk).exists():
            # Select only unknown rows and the fields displayed in this table.
            rows = SnapshotRecord.objects.filter(snapshot_id=latest.pk).filter(
                Q(data__membership='unknown') | Q(data__hit_status='unknown') | Q(data__status='unknown'))
            report = {'objects':[], 'dfw':{'rules':[], 'policies':[]}, 'tags':{'objects':[]}}
            for row in rows.values('view','data__name','data__notes','data__membership','data__hit_status','data__status'):
                data = {key.removeprefix('data__'):value for key,value in row.items() if key.startswith('data__')}
                target = report['objects'] if row['view']=='inventory' else report['tags']['objects'] if row['view']=='tags' else report['dfw']['rules'] if data['hit_status'] else report['dfw']['policies']
                target.append(data)
            metadata = Snapshot.objects.filter(pk=latest.pk).values('report__dfw__errors',
                'report__tags__errors','report__tags__unsupported_conditions','report__search_coverage','report__limitations').get()
            for area in ('dfw','tags'):
                report[area]['errors'] = metadata['report__'+area+'__errors'] or []
            report['tags']['unsupported_conditions'] = metadata['report__tags__unsupported_conditions']
            report['search_coverage'] = metadata['report__search_coverage'] or {}
            report['limitations'] = metadata['report__limitations'] or 'Search coverage was not recorded.'
        else:
            report = latest.report
        for row in report.get('objects', []):
            if row.get('membership') == 'unknown':
                issues.append({'area': 'Group membership', 'name': row.get('name'), 'detail': '; '.join(row.get('notes', [])) or 'Membership is unknown.'})
        dfw = report.get('dfw', {})
        for rows, field, label in ((dfw.get('rules', []), 'hit_status', 'Rule counters'),
                                   (dfw.get('policies', []), 'status', 'Policy inventory'),
                                   (report.get('tags', {}).get('objects', []), 'status', 'Tag usage')):
            for row in rows:
                if row.get(field) == 'unknown':
                    issues.append({'area': label, 'name': row.get('name'), 'detail': '; '.join(row.get('notes', [])) or 'Evidence is unknown.'})
        for area in ('dfw', 'tags'):
            for error in report.get(area, {}).get('errors', []):
                issues.append({'area': area.upper(), 'name': 'Collection error', 'detail': error})
        if report.get('tags', {}).get('unsupported_conditions'):
            issues.append({'area': 'Tags', 'name': 'Unsupported conditions', 'detail': 'Some tag conditions could not be evaluated.'})
        search = report.get('search_coverage', {})
        if search.get('mode') != 'all_types':
            issues.append({'area': 'Search', 'name': 'Limited or unrecorded coverage', 'detail': report.get('limitations', 'Search coverage was not recorded.')})
        if latest.needs_review and not issues:
            issues.append({'area': 'Audit', 'name': 'Incomplete checks', 'detail': 'This snapshot is flagged for review; consult its report.'})
    return {'start': start, 'end': now, 'latest': latest, 'issues': issues, 'gaps': gaps,
            'snapshots': len(stamps), 'first': stamps[0] if stamps else None, 'last': stamps[-1] if stamps else None,
            'threshold_hours': round(threshold.total_seconds()/3600, 2), 'freshness': freshness(environment),
            'stale_data': latest is None or now-latest.generated_at > timedelta(hours=policy().stale_hours),
            'failed': environment.jobs.filter(status='failed', created_at__gte=start, created_at__lte=now),
            'active': environment.jobs.filter(status__in=['queued', 'running']).count()}
