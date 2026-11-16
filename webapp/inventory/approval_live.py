"""Worker-only GET requests for review evidence. No mutation APIs are used."""
from django.db import transaction
from django.utils import timezone
from .models import FindingLiveCheck, Finding, Environment
from .approval_evidence import LIMIT, differences


def interrupted(pk):
    FindingLiveCheck.objects.filter(pk=pk, status='running').update(status='failed', finished_at=timezone.now(),
        message='Verification stopped or exceeded its time limit. Retry the read-only check; no NSX changes were sent.')


def claim():
    with transaction.atomic():
        row = FindingLiveCheck.objects.select_for_update(skip_locked=True).filter(status='queued').order_by('requested_at').first()
        if row:
            row.status='running'; row.progress=5; row.message='Reading current object configuration.'
            row.save(update_fields=['status','progress','message'])
        return row


def run(pk):
    from . import collector
    from .deletion import connection, current_object, clean
    from .approval_evidence import display
    check=FindingLiveCheck.objects.select_related('finding__environment').get(pk=pk)
    if check.status != 'running': return
    f=check.finding
    result={'members': {}, 'facts': [], 'lists': {}, 'sections': [], 'changes': [], 'coverage': 'Incomplete'}
    status='failed'; message='Verification did not complete.'
    def progress(value, text):
        FindingLiveCheck.objects.filter(pk=pk,status='running').update(progress=value,message=text)
    def add(key, title, items):
        result['lists'][key]=items[:LIMIT]
        result['sections'].append({'key':key,'title':title,'count':len(items),'retained':min(len(items),LIMIT)})
    try:
        from .deletion import target_type
        kind=target_type(f.path)
        expected_type={'unused':'Group','empty_group':'Group','unused_service':'Service','zero_hits':'Rule','disabled':'Rule','empty_policy':'SecurityPolicy'}[f.kind]
        if kind != expected_type: raise ValueError('Finding type does not match its NSX object path.')
        with connection(f.environment) as client:
            obj=current_object(client,f.path)
            if obj is None:
                status='changed'
                result['changes'].append({'field':'Object existence','before':'Present in saved evidence','after':'Absent from NSX'})
                raise ValueError('The object is absent from NSX. Run a collection before continuing review.')
            if obj.get('path') != f.path or obj.get('resource_type') != kind:
                raise ValueError('NSX returned an unexpected object identity.')
            config=clean(obj)
            if '/rules/' in f.path:
                parent=client.get(f.path.rsplit('/rules/',1)[0])
                config={'rule':config,'policy':{k:parent.get(k) for k in ('scope','category','sequence_number','stateful')}}
            expected=f.evidence.get('configuration')
            if not expected: raise ValueError('Saved configuration is unavailable. Collect fresh evidence before verification.')
            result['changes']=differences(clean(expected),config)
            if (f.evidence.get('unique_id') and f.evidence['unique_id'] != obj.get('unique_id')) or (f.evidence.get('created_at') is not None and f.evidence['created_at'] != obj.get('_create_time')):
                result['changes'].append({'field':'Object identity','before':'Saved identity','after':'Different live identity'})
            progress(20,'Reading membership or child configuration.')
            errors=[]
            if f.kind in ('unused','empty_group'):
                counts=[]
                planned=collector.membership_endpoints(obj)
                for endpoint,title in [('virtual-machines','VM members'),('ip-addresses','IP members'),('logical-ports','Port members'),('logical-switches','Switch members')]:
                    if endpoint not in planned:
                        result['facts'].append([title,'Not applicable to this definition'])
                        continue
                    try:
                        rows=list(client.items(f.path+'/members/'+endpoint))
                        counts.append(len(rows))
                        import hashlib, json
                        identities=sorted(json.dumps({k:r[k] for k in ('id','path','external_id','ip_address','ip_addresses') if k in r},sort_keys=True) if isinstance(r,dict) else str(r) for r in rows)
                        result['members'][endpoint]={'count':len(rows),'digest':hashlib.sha256(json.dumps(identities).encode()).hexdigest()}
                        baseline=f.approvals.get('owner',{}).get('live_members',{}).get(endpoint)
                        if baseline and baseline!=result['members'][endpoint]:
                            result['changes'].append({'field':title,'before':str(baseline['count'])+' members at approval','after':str(len(rows))+' members; identities changed'})
                        add(endpoint,title,[{k:r[k] for k in ('display_name','name','id','path','ip_address','ip_addresses','external_id') if k in r} if isinstance(r,dict) else r for r in rows])
                        result['facts'].append([title,str(len(rows))])
                    except collector.AuditError as exc:
                        errors.append(title+': '+error_message(exc))
                        result['facts'].append([title,'Unavailable'])
                membership='Has members' if any(counts) else 'Confirmed empty' if len(counts)==len(planned) and not errors and not getattr(client,'inventory_warnings',[]) else 'Membership unavailable'
                result['facts'].append(['Membership',membership])
                if f.kind=='empty_group' and any(counts):
                    result['changes'].append({'field':'Membership','before':'Confirmed empty','after':'Has members'})
                add('definition','Live membership definition',obj.get('expression',[]))
            elif f.kind=='unused_service':
                add('service_entries','Live protocols, ports and nested services',obj.get('service_entries',[]))
            elif f.kind=='empty_policy':
                rules=collector.objects(client,f.path+'/rules')
                add('rules','Live contained rules',rules)
                result['facts'].extend([['Enabled rules',str(sum(not r.get('disabled',False) for r in rules))],['Disabled rules',str(sum(bool(r.get('disabled')) for r in rules))]])
                if rules: result['changes'].append({'field':'Rules','before':'Empty policy','after':str(len(rules))+' rules'})
            else:
                for key,title in [('action','Action'),('disabled','Disabled')]: result['facts'].append([title,display(obj.get(key))])
                for key in ('source_groups','destination_groups','services','scope'): add(key,key.replace('_',' ').title(),obj.get(key,[]))
                if f.kind=='zero_hits':
                    stats=collector.rule_statistics(client,obj)
                    result['facts'].extend([['Recorded hits',display(stats.get('hit_count'))],['Statistics checked',display(stats.get('statistics_checked_at'))]])
                    if stats.get('hit_status')=='traffic_recorded': result['changes'].append({'field':'Traffic','before':'Zero hits','after':'Traffic recorded'})
                    elif stats.get('hit_status')!='zero_hits': errors.append('Fresh zero-hit statistics are unavailable.')
            progress(50,'Checking configuration references. This may take several minutes.')
            domains=collector.objects(client,'/infra/domains')
            groups=[g for d in domains for g in collector.objects(client,d['path']+'/groups')]
            if f.kind in ('unused','empty_group'):
                explicit,issues,limitations=collector.membership_scope(obj,{g['path']:g for g in groups})
                errors.extend(str(issue) for issue in [*issues,*limitations])
                if explicit:
                    result['facts']=[(label,'Has configured IP/MAC members' if label=='Membership' else value) for label,value in result['facts']]
                    if f.kind=='empty_group': result['changes'].append({'field':'Configured members','before':'Empty group','after':'Explicit IP/MAC members'})
                elif (issues or limitations) and not any(counts):
                    result['facts']=[(label,'Membership unavailable' if label=='Membership' else value) for label,value in result['facts']]
            services=collector.objects(client,'/infra/services')
            resources,coverage=collector.verified_configuration_search(client,groups,services)
            refs=sorted(collector.collect_references(resources+groups+services,[obj])[f.path])
            add('references','Live referencing objects',refs)
            result['facts'].append(['References found',str(len(refs))])
            if coverage.get('mode')!='all_types' or coverage.get('errors') or coverage.get('failed'):
                errors.append('Reference search coverage is incomplete.')
            if not any(r.get('path')==f.path for r in resources): errors.append('Target is missing from the reference-search index.')
            new_refs=set(refs)-set(f.evidence.get('referenced_by') or [])
            if new_refs: result['changes'].append({'field':'New references','before':'Not in saved evidence','after':', '.join(sorted(new_refs))[:1000]})
            if getattr(client,'inventory_warnings',[]): errors.append('Inventory changed during verification; results are incomplete.')
            result['coverage']='Incomplete' if errors else 'Read-only checks completed; search remains eventually consistent.'
            result['errors']=errors
            status='changed' if result['changes'] else 'failed' if errors else 'succeeded'
            message='Changes detected. Run a collection and repeat the review.' if status=='changed' else 'Some checks were unavailable. Review details and retry or run a verified collection.' if errors else 'Current checks completed. Saved observations and final deletion checks still apply.'
    except Exception as exc:
        from .diagnostics import LOG
        LOG.warning('finding_verification check=%s failed type=%s', pk, type(exc).__name__)
        message=error_message(exc)
    progress(90,'Recording read-only results.')
    with transaction.atomic():
        Environment.objects.select_for_update().get(pk=f.environment_id)
        current=Finding.objects.select_for_update().get(pk=f.pk)
        check=FindingLiveCheck.objects.select_for_update().get(pk=pk)
        if check.status!='running': return
        if check.snapshot_id_text != str(current.snapshot_id or '') or check.revision != current.revision:
            status='stale'; message='Finding changed while verification ran. Start a new check against the current evidence.'
        if status=='changed':
            from .finding_workflow import invalidate, FIELDS
            invalidate(current,'Read-only verification detected changes; fresh collection and review required.')
            current.revision+=1; current.save(update_fields=['revision']+FIELDS)
        check.status=status; check.progress=100; check.message=message[:500]; check.result=result; check.finished_at=timezone.now(); check.save()
        from .audit_events import record
        record('finding.verification_completed','Finding',f.pk,outcome='success' if status=='succeeded' else 'failed',details={'status':status,'message':message[:500]})


def error_message(exc):
    from . import collector
    if isinstance(exc,collector.AuditError):
        if exc.status_code in (401,403): return collector.access_message(getattr(exc,'method',None),getattr(exc,'path',None),exc.status_code)
        return 'NSX read failed (HTTP {}). Retry or inspect worker diagnostics.'.format(exc.status_code or 'unavailable')
    if isinstance(exc,ValueError): return str(exc)[:500]
    from django.core.exceptions import ValidationError
    if isinstance(exc,ValidationError): return '; '.join(exc.messages)[:500]
    return 'Read-only verification failed. Inspect worker diagnostics and retry; no NSX changes were sent.'
