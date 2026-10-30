"""VM investigations and local rule proposals. No NSX mutation capability."""
import csv
import io
import json
from uuid import UUID
from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .models import Environment, SnapshotRecord, FirewallRuleProposal, SegmentationSavedView
from .workload_rules import investigate, vm_summary
from .audit_events import record
from .preferences import table_page_size


class PairForm(forms.Form):
    source = forms.CharField(max_length=4096, required=False, widget=forms.HiddenInput)
    destination = forms.CharField(max_length=4096, required=False, widget=forms.HiddenInput)
    protocol = forms.ChoiceField(required=False, choices=[('','Any protocol'),('TCP','TCP'),('UDP','UDP'),('ICMP','ICMP')])
    port = forms.IntegerField(required=False,min_value=0,max_value=65535,label='Destination port')
    disabled = forms.BooleanField(required=False,label='Include disabled rules')
    def clean(self):
        result=super().clean()
        if result.get('protocol')=='ICMP' and result.get('port') is not None:
            self.add_error('port','Destination ports do not apply to ICMP.')
        return result


class ProposalForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput,initial=0,min_value=0)
    name=forms.CharField(max_length=200,label='Proposed rule name')
    source=forms.ChoiceField(label='Source group')
    destination=forms.ChoiceField(label='Destination group')
    protocol=forms.ChoiceField(choices=[('TCP','TCP'),('UDP','UDP'),('ICMP','ICMP')])
    port=forms.IntegerField(required=False,min_value=0,max_value=65535,label='Destination port')
    rule_action=forms.ChoiceField(label='Proposed action',choices=[('ALLOW','Allow'),('DROP','Drop'),('REJECT','Reject')])
    policy=forms.ChoiceField(label='Destination policy')
    placement=forms.CharField(max_length=300,label='Intended placement',help_text='For example: before the application default-drop rule. No ordering is applied.')
    logging=forms.BooleanField(required=False,initial=True,label='Propose logging enabled')
    owner=forms.ModelChoiceField(queryset=get_user_model().objects.none(),required=False)
    justification=forms.CharField(widget=forms.Textarea(attrs={'rows':3}),max_length=5000,label='Business justification')
    acknowledge=forms.BooleanField(label='I understand group selection may affect more workloads than this VM pair. This is a local proposal only.')
    def __init__(self,*args,pair=None,policies=(),saved=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.order_fields(['source','destination','name','rule_action','protocol','port','policy','placement','logging','owner','justification','acknowledge','revision'])
        self.fields['owner'].queryset=get_user_model().objects.filter(is_active=True)
        for side in ('source','destination'):
            vm=(pair or {}).get(side,{})
            choices=[('', 'Choose an existing group')]
            available=set()
            for kind, label in [('dynamic', 'Dynamic — fewest tag conditions first'), ('static', 'Static'), ('mixed', 'Mixed / nested — review membership'), ('unknown', 'Unknown definition')]:
                options=[]
                for g in vm.get('groups', []):
                    if g['type'] != kind: continue
                    detail = f" · {g['tag_count']} tag conditions" if kind == 'dynamic' else ''
                    evidence = ' · potential membership' if g['potential'] else ' · saved membership evidence'
                    options.append((g['path'], g['name'] + detail + evidence))
                    available.add(g['path'])
                if options: choices.append((label, options))
            old=(saved or {}).get(side)
            if old and old not in available:
                choices.append((old, old+' — retained proposal endpoint; recheck current membership'))
            self.fields[side].help_text='Groups may include other VMs. Fewest tag conditions is a sorting aid, not a recommendation to use a broader group.'
            self.fields[side].choices=choices
        choices=[('','Choose destination policy')]+list(policies)
        old=(saved or {}).get('policy')
        if old and old not in {v for v,_ in choices}: choices.append((old,old+' — saved proposal policy; recheck existence'))
        self.fields['policy'].choices=choices
    def clean(self):
        data=super().clean()
        if data.get('protocol') in ('TCP','UDP') and data.get('port') is None: self.add_error('port','Specify a destination port for this proposal.')
        if data.get('protocol')=='ICMP' and data.get('port') is not None: self.add_error('port','ICMP has no destination port.')
        return data


def snapshot_for(request,env):
    snapshots=env.snapshots.filter(testing=False,imported=False).defer('report','html')
    value=request.GET.get('snapshot')
    if value:
        try: UUID(value)
        except ValueError: raise Http404('Invalid snapshot')
        return get_object_or_404(snapshots,pk=value)
    return snapshots.first()


def owned_proposals(user,env):
    qs=FirewallRuleProposal.objects.filter(environment=env).select_related('snapshot','creator','owner')
    return qs if user.is_superuser else qs.filter(Q(creator=user)|Q(owner=user))


def vm_record(snapshot,path):
    return SnapshotRecord.objects.filter(snapshot=snapshot,view='vms',data__path=path).first() if path else None


@login_required
@never_cache
@require_http_methods(['GET'])
def vm_search(request,pk):
    env=get_object_or_404(Environment,pk=pk);snapshot=snapshot_for(request,env)
    if not snapshot or snapshot.browsing_status!='ready': return JsonResponse({'results':[],'message':'Restore or collect snapshot evidence first.'})
    term=request.GET.get('q','').strip()[:200]
    qs=SnapshotRecord.objects.filter(snapshot=snapshot,view='vms')
    if term: qs=qs.filter(Q(name__icontains=term)|Q(data__vm_details__icontains=term)|Q(data__tags__icontains=term)|Q(data__path__icontains=term))
    rows=list(qs.order_by('sort_name','pk').only('pk','name','data')[:26])
    return JsonResponse({'results':[{'value':v['path'],'name':v['name'],'ips':v['ips'],'environment':env.name} for v in map(vm_summary,rows[:25])],'more':len(rows)>25})


@login_required
@never_cache
@require_http_methods(['GET','POST'])
def pair_view(request,pk):
    env=get_object_or_404(Environment,pk=pk);snapshot=snapshot_for(request,env)
    if snapshot and not request.GET.get('snapshot'):
        params=request.GET.copy();params['snapshot']=str(snapshot.pk)
        return redirect(reverse('segmentation-pair',args=[pk])+'?'+params.urlencode())
    form=PairForm(request.GET);pair=None;source=None;destination=None
    ready=snapshot and snapshot.browsing_status=='ready'
    if ready and form.is_valid():
        source=vm_record(snapshot,form.cleaned_data['source']);destination=vm_record(snapshot,form.cleaned_data['destination'])
        for side,value in [('source',source),('destination',destination)]:
            if form.cleaned_data[side] and not value: form.add_error(None,f'The selected {side} VM is not present in this snapshot. Choose it again.')
        if source and destination:
            pair=investigate(snapshot,source,destination,form.cleaned_data['protocol'],str(form.cleaned_data['port']) if form.cleaned_data['port'] is not None else '',form.cleaned_data['disabled'])
    if request.method=='POST':
        if request.POST.get('action')=='delete_pair':
            value=request.POST.get('view_id','')
            if value.isdigit(): SegmentationSavedView.objects.filter(pk=value,user=request.user,environment=env,filters__workspace='pair').delete()
        if request.POST.get('action')=='save_pair' and pair:
            name=request.POST.get('name','').strip()[:100]
            if name:
                filters={k:request.GET.get(k,'') for k in ('source','destination','protocol','port','disabled')};filters['workspace']='pair'
                SegmentationSavedView.objects.create(user=request.user,environment=env,name=name,filters=filters)
                messages.success(request,'VM investigation saved. Opening it uses the latest full collection.')
        return redirect(request.get_full_path())
    candidates=pair['candidates'] if pair else [];unresolved=pair['unresolved'] if pair else []
    for rule in candidates+unresolved:
        rule['object_url']=reverse('snapshot',args=[snapshot.pk])+'?'+urlencode({'object':rule['path']})
    if request.GET.get('export')=='csv' and pair:
        out=io.StringIO();w=csv.writer(out);w.writerow(['Snapshot','Source VM','Destination VM','Status','Rule','Policy','Action','Source explanation','Destination explanation','Service explanation','Scope'])
        def safe(v):
            value=str(v);return "'"+value if value.startswith(('=','+','-','@','\t','\r')) else value
        for r in candidates+unresolved: w.writerow([safe(v) for v in [snapshot.pk,pair['source']['name'],pair['destination']['name'],r['status'],r['name'],r['policy'],r['action'],r['source_reason'],r['destination_reason'],r['service_reason'],r['scope_status']]])
        response=HttpResponse(out.getvalue(),content_type='text/csv');response['Content-Disposition']='attachment; filename="workload-rule-evidence.csv"';return response
    params=request.GET.copy();params.pop('page',None);params.pop('export',None)
    draft_url=reverse('segmentation-proposal-new',args=[pk])+'?'+params.urlencode()
    status_urls={}
    for category in ('candidate','unresolved'):
        query=params.copy();query['status']=category;status_urls[category]='?'+query.urlencode()
    query=params.copy();query['export']='csv';export_url='?'+query.urlencode()
    selected_status=request.GET.get('status','candidate')
    page=Paginator(unresolved if selected_status=='unresolved' else candidates,table_page_size(request)).get_page(request.GET.get('page'))
    saved=[{'id':v.pk,'name':v.name,'url':reverse('segmentation-pair',args=[pk])+'?'+urlencode({k:val for k,val in v.filters.items() if k!='workspace'})} for v in SegmentationSavedView.objects.filter(environment=env,user=request.user,filters__workspace='pair')]
    return render(request,'inventory/workload_pair.html',dict(environment=env,snapshot=snapshot,ready=ready,form=form,pair=pair,
        snapshots=env.snapshots.filter(testing=False,imported=False).only('pk','generated_at')[:50],source_vm=vm_summary(source) if source else None,destination_vm=vm_summary(destination) if destination else None,
        status_urls=status_urls,export_url=export_url,page=page,status=selected_status,candidate_count=len(candidates),unresolved_count=len(unresolved),draft_url=draft_url,saved_views=saved,
        proposals=owned_proposals(request.user,env).order_by('-updated_at')[:20]))


@login_required
@never_cache
@require_http_methods(['GET','POST'])
def proposal(request,pk,proposal_id=None):
    env=get_object_or_404(Environment,pk=pk)
    existing=get_object_or_404(owned_proposals(request.user,env),pk=proposal_id) if proposal_id else None
    snapshot=existing.snapshot if existing else snapshot_for(request,env)
    if not existing and (not snapshot or snapshot.browsing_status!='ready'): raise Http404('A browsable snapshot is required to prepare a proposal.')
    saved=existing.configuration if existing else {}
    source_path=saved.get('source_vm',request.GET.get('source',''));destination_path=saved.get('destination_vm',request.GET.get('destination',''))
    src=vm_record(snapshot,source_path) if snapshot else None;dst=vm_record(snapshot,destination_path) if snapshot else None
    pair=investigate(snapshot,src,dst) if src and dst and snapshot.browsing_status=='ready' else None
    if not existing and not pair: raise Http404('Choose both VMs in the same saved snapshot first.')
    policies=[]
    if snapshot and snapshot.browsing_status=='ready':
        policies=list(SnapshotRecord.objects.filter(snapshot=snapshot,data__configuration__resource_type='SecurityPolicy').values_list('data__path','name'))
        # Older indexed policy rows may omit resource_type. Derive known parent policy identities from rules.
        if not policies:
            policies=sorted({(r['data__policy_path'],r['data__policy_name'] or r['data__policy_path']) for r in SnapshotRecord.objects.filter(snapshot=snapshot,data__configuration__rule__isnull=False).values('data__policy_path','data__policy_name') if r['data__policy_path']})
    initial=dict(saved,rule_action=saved.get('action','ALLOW'),name=existing.name if existing else f"Allow {src.name} to {dst.name}"[:200],justification=existing.justification if existing else '',owner=existing.owner_id if existing else request.user.pk,revision=existing.revision if existing else 0)
    if not existing: initial.update(protocol=request.GET.get('protocol') or 'TCP',port=request.GET.get('port',''),action='ALLOW',placement='Review rule ordering before deployment',logging=True)
    form=ProposalForm(request.POST if request.method=='POST' and request.POST.get('action')=='save' else None,initial=initial,pair=pair,policies=policies,saved=saved)
    if request.method=='POST':
        if existing and request.POST.get('action')=='discard':
            with transaction.atomic():
                locked=FirewallRuleProposal.objects.select_for_update().get(pk=existing.pk)
                if str(locked.revision)!=request.POST.get('revision'): messages.error(request,'This proposal changed. Reload before discarding.')
                else:
                    locked.state='discarded';locked.revision+=1;locked.save()
                    record('segmentation.proposal_discarded','FirewallRuleProposal',locked.pk,actor=request.user.pk,details={'revision':locked.revision})
                    messages.success(request,'Proposal discarded. Nothing was sent to NSX.')
            return redirect('segmentation-proposal',pk=pk,proposal_id=existing.pk)
        if request.POST.get('action')=='save' and form.is_valid():
            with transaction.atomic():
                obj=FirewallRuleProposal.objects.select_for_update().get(pk=existing.pk) if existing else FirewallRuleProposal(environment=env,snapshot=snapshot,creator=request.user)
                if existing and (obj.state!='draft' or obj.revision!=form.cleaned_data['revision']): form.add_error(None,'Proposal changed or was discarded. Reload before editing.')
                else:
                    clean=form.cleaned_data
                    obj.name=clean['name'];obj.owner=clean['owner'];obj.justification=clean['justification']
                    obj.configuration={k:clean[k] for k in ('source','destination','protocol','port','policy','placement','logging')}
                    obj.configuration.update(action=clean['rule_action'],source_vm=source_path,destination_vm=destination_path,source_name=src.name if src else saved.get('source_name'),destination_name=dst.name if dst else saved.get('destination_name'),snapshot=str(snapshot.pk) if snapshot else saved.get('snapshot'),evidence_at=str(snapshot.generated_at) if snapshot else saved.get('evidence_at'))
                    if existing: obj.revision+=1
                    obj.save();record('segmentation.proposal_saved','FirewallRuleProposal',obj.pk,actor=request.user.pk,details={'revision':obj.revision,'configuration':obj.configuration,'justification':obj.justification})
                    messages.success(request,'Rule proposal saved locally. It is not deployed to NSX.')
                    return redirect('segmentation-proposal',pk=pk,proposal_id=obj.pk)
    if existing and request.GET.get('export')=='json':
        content={'kind':'local_firewall_rule_proposal','deployed':False,'state':existing.state,'revision':existing.revision,'name':existing.name,'environment':env.name,'owner':str(existing.owner or ''),'justification':existing.justification,'configuration':existing.configuration}
        response=HttpResponse(json.dumps(content,indent=2),content_type='application/json');response['Content-Disposition']='attachment; filename="firewall-rule-proposal.json"';return response
    # Similar rules are candidates for the VM pair, not a duplicate/existence verdict for the new proposal.
    latest=env.snapshots.filter(testing=False,imported=False).only('pk').first()
    return render(request,'inventory/firewall_proposal.html',dict(environment=env,snapshot=snapshot,proposal=existing,form=form,pair=pair,
        similar=(pair['candidates'][:20] if pair else []),similar_total=len(pair['candidates']) if pair else 0,
        stale=not snapshot or bool(latest and latest.pk!=snapshot.pk)))


@login_required
@never_cache
@require_http_methods(['POST'])
def verify_membership(request,pk):
    """One bounded read-only VM membership page; never infer absence from a partial page."""
    if not request.user.is_staff: return HttpResponseForbidden('Operator access required.')
    from .deletion import connection, target_type
    from django.core.exceptions import ValidationError
    env=get_object_or_404(Environment,pk=pk);snapshot=snapshot_for(request,env)
    if not snapshot or snapshot.browsing_status!='ready': return JsonResponse({'error':'Restore saved evidence first.'},status=409)
    vm=get_object_or_404(SnapshotRecord,snapshot=snapshot,view='vms',data__path=request.POST.get('vm',''))
    group=get_object_or_404(SnapshotRecord,snapshot=snapshot,data__kind='group',data__path=request.POST.get('group',''))
    path=group.data.get('path','')
    try:
        if target_type(path)!='Group': raise ValidationError('Unsupported group path')
        identities={x for x in (vm.data.get('vm_details',{}).get('external_id'),vm.data.get('vm_details',{}).get('id')) if x}
        if not identities: return JsonResponse({'error':'Saved VM identity is unavailable. A name alone cannot verify membership.'},status=409)
        with connection(env) as client:
            page=client.get(path+'/members/virtual-machines',{'page_size':1000})
        rows=page.get('results') if isinstance(page,dict) else None
        if not isinstance(rows,list): raise ValueError('Malformed membership response')
        matched=any(isinstance(row,dict) and identities.intersection({row.get('external_id'),row.get('id')}) for row in rows)
        text='VM identity found in the live group VM-membership response.' if matched else 'VM identity not found in this response. Additional pages or other membership types may apply; absence is not established.'
        record('segmentation.membership_checked','Environment',env.pk,actor=request.user.pk,details={'group':path,'vm':vm.data.get('path'),'present':matched})
        return JsonResponse({'present':matched,'message':text,'checked_at':timezone.now().isoformat(),'partial':bool(page.get('cursor')),'note':'Read-only membership check. Saved candidate rules and effective access are not re-evaluated.'})
    except Exception as exc:
        code=getattr(exc,'status',None) or getattr(exc,'status_code',None)
        record('segmentation.membership_check_failed','Environment',env.pk,actor=request.user.pk,outcome='failed',details={'group':path,'error_type':type(exc).__name__,'http_status':code})
        return JsonResponse({'error':'NSX membership read failed'+(f' (HTTP {code})' if code else '')+'. Review account permissions and environment connectivity. No membership conclusion was made.'},status=502)
