"""Canonical reference exports. All reads are local; never refresh NSX evidence."""
from django.http import Http404
from .models import SnapshotRecord

SECTIONS = {
    'vms': {'resolved_groups':'Groups', 'network_groups':'Network links', 'confirmed_rules':'Firewall rules', 'ip_groups':'IP/subnet matches'},
    'inventory': {'firewall':'Firewall rules', 'objects':'Other objects'},
    'tags': {'vms':'VM assignments', 'group_conditions':'Group conditions', 'group_assignments':'Group tags',
             'other_assignments':'Other resources', 'firewall_references':'Firewall rules'},
    'scopes': {'tags':'Tags in this scope'},
}
MAX_ROWS = 100000
LIMIT_NOTE = 'Configuration references are not an effective-policy or observed-traffic verdict. Partial or unavailable evidence can omit relationships.'


def owner(snapshot, options):
    view = options.get('view')
    if view not in SECTIONS:
        raise ValueError('This object does not support reference exports.')
    rows = SnapshotRecord.objects.filter(snapshot=snapshot, view=view)
    if view == 'inventory':
        path = options.get('path')
        if not isinstance(path, str) or len(path)>4096:
            raise ValueError('Choose an inventory object.')
        rows = rows.filter(compact__path=path, data__kind__in=['group','custom_service'])
    else:
        ordinal = options.get('id')
        if type(ordinal) is not int or ordinal<0:
            raise ValueError('Choose an inventory object.')
        rows = rows.filter(ordinal=ordinal)
    result = rows.only('id','ordinal','view','name','compact').first()
    if result is None:
        raise Http404('Object is not available in this snapshot.')
    return result


def validate(snapshot, values):
    if not isinstance(values,dict): raise ValueError('Invalid export request.')
    options = {key:values.get(key) for key in ('view','path','id','section','format','source')}
    record = owner(snapshot, options)
    if options['section'] not in ('all', *SECTIONS[record.view]): raise ValueError('Choose a reference category.')
    if options['format'] not in ('csv','pdf','json','yaml','html'): raise ValueError('Choose a supported format.')
    if options['source'] not in ('saved_snapshot','current_cache'): raise ValueError('Choose an evidence source.')
    if options['source']=='current_cache' and record.view!='vms': raise ValueError('Cached current observations are available for VMs only.')
    options['object_name']=record.name
    return options


def collect_pages(read, budget):
    items, first, page = [], None, 0
    while True:
        result = read(page)
        if first is None: first = {key:value for key,value in result.items() if key not in ('items','page','has_next')}
        items.extend(result.get('items',[]))
        if len(items)>budget: raise ValueError('Export exceeds 100,000 references. Export one category at a time.')
        if not result.get('has_next'): return items, first or {}
        page += 1
        if not result.get('items'): raise ValueError('Reference paging stopped unexpectedly. Retry the export.')


def build(job):
    from . import vm_relationships, object_references, tag_relationships, confirmed_references, ip_references
    snapshot, options = job.snapshot, job.options
    record = owner(snapshot, options)
    selected = SECTIONS[record.view]
    keys = list(selected) if options['section']=='all' else [options['section']]
    current = options['source']=='current_cache'
    context, ip, current_metadata = None, None, {}
    if record.view=='vms':
        if current:
            direct = {edge['path']:{**edge,'membership_basis':'direct_vm','membership_source':'current_nsx'}
                      for edge in job.observation.get('items',[]) if isinstance(edge,dict) and isinstance(edge.get('path'),str)}
            context, current_metadata = confirmed_references.current_context(snapshot, {'ordinal':record.ordinal},direct=direct,full=True)
        else:
            edges = SnapshotRecord.objects.filter(pk=record.pk).values_list('data__resolved_groups',flat=True).get() or []
            context = {edge['path']:{'membership_basis':'direct_vm',**edge} for edge in edges if isinstance(edge,dict) and isinstance(edge.get('path'),str)}
            if 'confirmed_rules' in keys:
                context, current_metadata = confirmed_references.with_ip_context(snapshot.pk, record.ordinal, context,full=True)
        if 'ip_groups' in keys:
            ip, ip_metadata = ip_references.context(snapshot.pk,record.ordinal,full=True)
    sections, total = [], 0
    for key in keys:
        source = 'saved_snapshot'
        notes = []
        if record.view=='vms' and key=='confirmed_rules':
            reader = lambda page: confirmed_references.read(snapshot.pk,list(context),page,evidence=context,full=True)
            status = job.observation.get('status','not_collected') if current else record.compact.get('resolved_membership_status','not_collected')
            if current_metadata.get('ip_status') not in (None,'complete','not_applicable'): status='partial'
            notes = [LIMIT_NOTE,*current_metadata.get('ip_notes',[])]
            if current: source='current_direct_and_saved_configuration'
            checked = job.observation.get('checked_at') if current else record.compact.get('resolved_membership_checked_at')
            meta = {'status':status,'checked_at':checked,**({'stale':job.observation.get('stale')} if current else {})}
            if current: notes.extend(job.observation.get('notes') or [])
        elif record.view=='vms' and key=='resolved_groups' and current:
            values = sorted((edge for edge in context.values() if edge.get('membership_basis')),key=lambda edge:edge['path'])
            reader = lambda page: {'items':values[page*25:(page+1)*25],'has_next':len(values)>(page+1)*25}
            meta = {key:job.observation.get(key) for key in ('status','checked_at','stale','notes')}
            if current_metadata.get('network_status') not in ('complete','not_applicable'): meta['status']='partial'
            source = 'current_direct_and_saved_attachments'
            notes = ['Current direct observations are frozen when the export is requested. Interface attachments retain their saved snapshot timestamps.']
        elif record.view=='vms' and key=='ip_groups':
            values = sorted(ip.values(),key=lambda edge:edge['path'])
            reader = lambda page: {'items':values[page*25:(page+1)*25],'has_next':len(values)>(page+1)*25}
            meta = {'status':ip_metadata['ip_status'],'checked_at':ip_metadata.get('ip_checked_at'),'notes':ip_metadata.get('ip_notes',[])}
        elif record.view=='vms':
            reader = lambda page: vm_relationships.read(snapshot.pk,record.ordinal,key,page)
            meta = {}
        elif record.view=='inventory':
            reader = lambda page: object_references.read(snapshot.pk, options['path'],key,page,full=True)
            meta = {'status':'saved_configuration','checked_at':snapshot.generated_at.isoformat()}
            notes = ['References retained in the saved inventory. An empty list does not establish complete NSX coverage.']
        else:
            reader = lambda page: tag_relationships.read(snapshot.pk,record.ordinal,key,page,context=True,full=True)
            meta = {'status':'saved_configuration','checked_at':snapshot.generated_at.isoformat()}
            notes = ['Tag assignments and tag conditions do not establish VM group membership.']
        items, reader_meta = collect_pages(reader,MAX_ROWS-total)
        total += len(items)
        sections.append({'key':key,'label':selected[key],'source':source,**reader_meta,**meta,
                         'notes':list(dict.fromkeys([*(reader_meta.get('notes') or []),*(meta.get('notes') or []),*notes])),
                         'count':len(items),'items':items})
    from django.utils import timezone
    from .models import SnapshotCoverage
    kind=(SnapshotRecord.objects.filter(pk=record.pk).values_list('data__kind',flat=True).get() if record.view=='inventory' else {'vms':'vm','tags':'tag','scopes':'scope'}[record.view])
    coverage=SnapshotCoverage.objects.filter(snapshot=snapshot).values_list('issue_count',flat=True).first()
    return {'schema_version':1,'metadata':{'environment':snapshot.environment.name,'environment_id':snapshot.environment_id,'snapshot_id':str(snapshot.pk),
        'collected_at':snapshot.generated_at.isoformat(),'requested_at':job.created_at.isoformat(),'prepared_at':timezone.now().isoformat(),
        'object':{'name':record.name,'type':kind,'view':record.view,'ordinal':record.ordinal,'path':record.compact.get('path')},
        'evidence':options['source'],'coverage_issue_count':coverage,'snapshot_needs_review':snapshot.needs_review,
        'note':LIMIT_NOTE+(' Collection coverage: '+str(coverage)+' recorded issues.' if coverage is not None else ' Collection coverage summary was not retained.'),'reference_count':total,
        'current_observation':{key:job.observation.get(key) for key in ('status','checked_at','stale')} if current else None},'sections':sections}
