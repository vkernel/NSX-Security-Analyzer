"""Snapshot-pinned segmentation workspace. All mutations are local review metadata."""
import csv
import io
from datetime import date
from uuid import UUID
from django.http import Http404
from django.core.paginator import Paginator
from .preferences import table_page_size
from urllib.parse import urlencode, quote_plus
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from .page_response import render, analyze
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .models import Environment, Snapshot, SegmentationSettings, SegmentationBoundary, SegmentationReview, SegmentationSavedView
from .audit_events import record
from .segmentation import build
from .application_maps import cached_map, VERSION as MAP_VERSION


class ReviewForm(forms.ModelForm):
    class Meta:
        model = SegmentationReview
        fields = ['owner','state','note','expires_at']
        widgets = {'expires_at':forms.DateInput(attrs={'type':'date'})}
    def clean(self):
        data=super().clean()
        if data.get('state')=='exception' and (not data.get('note') or not data.get('expires_at') or data['expires_at']<=date.today()):
            raise forms.ValidationError('An exception requires a reason and a future expiry date.')
        return data


@login_required
@require_http_methods(['GET','POST'])
def workspace(request, pk=None):
    if pk is None:
        env = Environment.objects.filter(pk=request.GET.get('environment')).first() if request.GET.get('environment','').isdigit() else Environment.objects.first()
        if not env: return render(request,'inventory/segmentation.html',{})
        latest_id=env.snapshots.filter(testing=False,imported=False).values_list('pk',flat=True).first()
        return redirect(reverse('segmentation',args=[env.pk])+('?' + urlencode({'snapshot':latest_id}) if latest_id else ''))
    env=get_object_or_404(Environment,pk=pk)
    snapshots=env.snapshots.filter(testing=False, imported=False).defer('report','html','summary')
    if request.GET.get('check')=='1':
        latest_id=snapshots.values_list('pk',flat=True).first()
        response=JsonResponse({'latest':str(latest_id) if latest_id else ''})
        response['Cache-Control']='private, no-store'
        return response
    latest=snapshots.first()
    if request.GET.get('snapshot'):
        try: UUID(request.GET['snapshot'])
        except ValueError: raise Http404('Invalid snapshot')
    snap=get_object_or_404(snapshots,pk=request.GET['snapshot']) if request.GET.get('snapshot') else latest
    if snap and not request.GET.get('snapshot'):
        query=request.GET.copy();query['snapshot']=str(snap.pk)
        return redirect(reverse('segmentation',args=[pk])+'?'+query.urlencode())
    settings=SegmentationSettings.objects.filter(environment=env).first()
    scope=settings.grouping_scope if settings else ''
    filters={k:request.GET.get(k,'')[:255] for k in ('q','service','tab','disabled','hide')}
    if filters['tab'] not in ('matrix','map','priorities','changes','settings'): filters['tab']='map'
    filters['groups'] = list(dict.fromkeys(request.GET.getlist('groups')))[:12]
    ready=snap and snap.browsing_status=='ready'
    rules_needed=bool(filters['tab']!='settings' and (filters['tab']!='matrix' or filters['groups']) or request.GET.get('rule') or request.GET.get('export'))
    map_data=analyze(request,cached_map,snap,filters['q'],scope,filters['service'],filters['disabled']=='1',filters['hide'].split(',')) if ready and filters['tab']=='map' else None
    if map_data is not None and request.method=='GET' and not request.GET.get('rule') and not request.GET.get('export'):
        data=map_data
    else:
        data=analyze(request,build,snap,filters['q'],scope,filters['service'],filters['disabled']=='1',filters['hide'].split(','), selected_paths=filters['groups'] if filters['tab']=='matrix' else None, application_mode=filters['tab']=='map', rules_needed=rules_needed, application_graph_data=map_data['applications'] if map_data else None) if ready else {}
        if map_data is not None:
            data.update(map_context=map_data['map_context'],map_source_stamp=map_data['map_source_stamp'])
    coverage_warning=Snapshot.objects.filter(pk=snap.pk).values_list('summary__inventory_warnings',flat=True).first() if snap else False
    if snap and (snap.needs_review or coverage_warning):
        data.setdefault('warnings',[]).append('This snapshot contains collection warnings. Inspect collection coverage before relying on these relationships.')
    rules={r['path']:r for r in data.get('rules',[])}
    selected=rules.get(request.GET.get('rule',''))
    form=ReviewForm(instance=SegmentationReview.objects.filter(environment=env,rule_path=selected['path']).first() if selected else None)
    form.fields['owner'].queryset=get_user_model().objects.filter(is_active=True,is_staff=True)
    redisplay=False
    if request.method=='POST':
        action=request.POST.get('action')
        if action=='save_view':
            name=request.POST.get('name','').strip()[:100]
            if name:
                SegmentationSavedView.objects.create(user=request.user,environment=env,name=name,filters=filters)
                messages.success(request,'Personal view saved. It opens the latest collection.')
        elif action=='delete_view':
            SegmentationSavedView.objects.filter(pk=request.POST.get('view_id'),user=request.user,environment=env).delete()
        else:
            if not request.user.is_staff: return HttpResponseForbidden('Operator access required.')
            if action in ('settings','boundary','remove_boundary') and not request.user.is_superuser: return HttpResponseForbidden('Administrator access required.')
            if action=='settings':
                SegmentationSettings.objects.update_or_create(environment=env,defaults={'grouping_scope':request.POST.get('scope','').strip()[:255]})
            elif action=='boundary':
                source=request.POST.get('source','');destination=request.POST.get('destination','')
                paths={g['path'] for g in data.get('group_catalog',[])}
                if source in paths and destination in paths:
                    SegmentationBoundary.objects.create(environment=env,name=request.POST.get('name','Boundary')[:200],source=source,destination=destination)
                else: messages.error(request,'Select source and destination from the displayed groups.')
            elif action=='remove_boundary':
                SegmentationBoundary.objects.filter(pk=request.POST.get('boundary_id'),environment=env).delete()
            elif action=='review' and selected:
                form=ReviewForm(request.POST,instance=SegmentationReview.objects.filter(environment=env,rule_path=selected['path']).first())
                form.fields['owner'].queryset=get_user_model().objects.filter(is_active=True,is_staff=True)
                if request.POST.get('fingerprint')!=selected['fingerprint']: messages.error(request,'Evidence changed. Reload before recording a review.')
                elif form.is_valid():
                    review=form.save(commit=False);review.environment=env;review.rule_path=selected['path'];review.fingerprint=selected['fingerprint'];review.save()
                    record('segmentation.review_updated','SegmentationReview',review.pk,actor=request.user.pk,details={'state':review.state,'snapshot':str(snap.pk),'note':review.note,'owner':review.owner_id,'expires_at':str(review.expires_at or ''),'fingerprint':review.fingerprint})
                    messages.success(request,'Review recorded. No NSX configuration was changed.')
                    return redirect(request.get_full_path())
                else: redisplay=True
            if not redisplay:
                record('segmentation.'+str(action),'Environment',env.pk,actor=request.user.pk,details={'snapshot':str(snap.pk) if snap else ''})
        if not redisplay: return redirect(request.get_full_path())
    boundaries=list(SegmentationBoundary.objects.filter(environment=env,enabled=True)) if filters['tab'] in ('priorities','settings') else []
    deviations=[]
    for boundary in boundaries:
        matches=[r for r in rules.values() if not r['disabled'] and r['action']=='ALLOW' and not (r['configuration']['rule'].get('sources_excluded') or r['configuration']['rule'].get('destinations_excluded')) and (boundary.source in r['sources'] or 'ANY' in r['sources']) and (boundary.destination in r['destinations'] or 'ANY' in r['destinations'])] if filters['tab']=='priorities' else []
        deviations.append({'boundary':boundary,'rules':matches})
    priority_page=Paginator(data.get('priorities',[]),table_page_size(request)).get_page(request.GET.get('page'))
    displayed={r['path']:r for r in priority_page} if filters['tab']=='priorities' else {}
    if selected: displayed[selected['path']]=selected
    for edge in data.get('applications',{}).get('edges',[]) + data.get('edges',[]) + deviations:
        displayed.update({r['path']:r for r in edge['rules']})
    reviews={r.rule_path:r for r in SegmentationReview.objects.filter(environment=env,rule_path__in=list(displayed))}
    object_base=reverse('snapshot',args=[snap.pk])+'?object=' if snap else ''
    params=request.GET.copy();params.pop('rule',None)
    rule_base='?'+params.urlencode()+'&rule='
    for row in displayed.values():
        review=reviews.get(row['path']);row['review']=review
        row['review_stale']=bool(review and (review.fingerprint!=row['fingerprint'] or review.state=='exception' and review.expires_at and review.expires_at<=date.today()))
        row['url']=rule_base+quote_plus(row['path'])
        row['object_url']=object_base+quote_plus(row['path'])
    application_scopes=sorted({t['scope'] for g in data.get('group_catalog',[]) for t in g['evidence']['tags']})
    for app in data.get('applications', {}).get('nodes', []):
        for group in app['groups'][:50]:
            group['url']=object_base+quote_plus(group['path'])
        app['matrix_url']='?'+urlencode({'snapshot':str(snap.pk),'tab':'matrix','groups':[g['path'] for g in app['groups'][:12]]},doseq=True)
    if map_data is not None:
        context=data['map_context']
        detail_base=reverse('segmentation-connection',args=[env.pk])
        for edge in data['applications']['edges']:
            edge['details_url']=detail_base+'?'+urlencode({'snapshot':str(snap.pk),'source':edge['source']['name'],'destination':edge['destination']['name'],
                'stamp':data['map_source_stamp'],'version':MAP_VERSION,'q':context['query'],'scope':context['scope'],'service':context['service'],
                'disabled':'1' if context['include_disabled'] else '', 'hide':','.join(context['hidden_paths'])})
    changes=[];group_changes=[];previous=None
    if ready and filters['tab']=='changes':
        previous=snapshots.filter(generated_at__lt=snap.generated_at,browsing_status='ready').first()
        if previous:
            old=analyze(request,build,previous,filters['q'],scope,filters['service'],filters['disabled']=='1',filters['hide'].split(','))
            before={r['path']:r for r in old['rules']}
            old_groups={g['path']:g for g in old['groups']}
            for g in data['groups']:
                prior=old_groups.get(g['path'])
                if prior and (g.get('definition')!=prior.get('definition') or g.get('membership')!=prior.get('membership')):
                    group_changes.append({'name':g['name'],'before':prior,'after':g})
            for path in sorted(set(before)|set(rules)):
                a=before.get(path);b=rules.get(path)
                if not a or not b or a['fingerprint']!=b['fingerprint']:
                    changes.append({'name':(b or a)['name'],'state':'Added to view' if not a else 'Removed from view' if not b else 'Configuration changed','before':a,'after':b})
    if request.GET.get('export')=='csv':
        out=io.StringIO();writer=csv.writer(out);writer.writerow(['Snapshot','Rule','Action','Sources','Destinations','Services','Review reasons'])
        def safe(v):
            v=str(v);return "'"+v if v.startswith(('=','+','-','@','\t','\r')) else v
        for r in rules.values(): writer.writerow([safe(v) for v in [snap.pk,r['name'],r['action'],r['sources'],r['destinations'],[r['services'],r['inline']],r['reasons']]])
        response=HttpResponse(out.getvalue(),content_type='text/csv');response['Content-Disposition']='attachment; filename="segmentation.csv"';return response
    tabs=[]
    for key,label in [('priorities','Review priorities'),('changes','Changes'),('settings','Application grouping & boundaries')]:
        params=request.GET.copy();params['tab']=key;params.pop('rule',None);tabs.append({'name':label,'key':key,'url':'?'+params.urlencode()})
    saved=[]
    for view in SegmentationSavedView.objects.filter(user=request.user,environment=env).exclude(filters__workspace='pair'): saved.append({'item':view,'url':reverse('segmentation',args=[pk])+'?'+urlencode(view.filters, doseq=True)})
    if len(request.GET.getlist('groups')) > 12:
        data.setdefault('warnings', []).append('Select up to 12 groups. Only the first 12 selections are included.')
    catalog=data.get('group_catalog',[])
    catalog_total=len(catalog)
    data['group_catalog']=[g for i,g in enumerate(catalog) if i<50 or g['selected']]
    return render(request,'inventory/segmentation.html',dict(application_scopes=application_scopes,priority_page=priority_page,group_changes=group_changes,environment=env,snapshot=snap,latest=latest,snapshots=snapshots[:50],ready=ready,
        catalog_total=catalog_total,scope=scope,filters=filters,tabs=tabs,selected=selected,review_form=form,deviations=deviations,saved_views=saved,changes=changes,previous=previous,**data))


@login_required
@require_http_methods(['GET'])
def group_search(request, pk):
    from .workload_views import snapshot_for
    from .analysis_index import load
    env=get_object_or_404(Environment,pk=pk)
    snapshot=snapshot_for(request,env)
    results=[]
    if snapshot and snapshot.browsing_status=='ready':
        term=request.GET.get('q','').strip()[:200].casefold()
        for group in load(snapshot,'groups')['groups']:
            if term and term not in (group['name']+' '+group['path']).casefold(): continue
            results.append({'path':group['path'],'name':group['name'],'type':group['evidence']['group_type']})
        results.sort(key=lambda g:(g['name'].casefold(),g['path']))
    response=JsonResponse({'results':results[:50],'total':len(results),'more':len(results)>50})
    response['Cache-Control']='private, no-store'
    return response


@login_required
@require_http_methods(['GET'])
def connection_details(request,pk):
    from .workload_views import snapshot_for
    from .analysis_index import load, VERSION
    from .models import SnapshotAnalysisIndex
    env=get_object_or_404(Environment,pk=pk)
    if not request.GET.get('snapshot'): raise Http404('Choose a pinned snapshot')
    snapshot=snapshot_for(request,env)
    reload_url=reverse('segmentation',args=[pk])+'?'+urlencode({'snapshot':snapshot.pk,'tab':'map'})
    projection=SnapshotAnalysisIndex.objects.filter(snapshot=snapshot).values('version','updated_at').first()
    if snapshot.browsing_status!='ready' or not projection or projection['version']!=VERSION or request.GET.get('version')!=str(MAP_VERSION) or request.GET.get('stamp')!=projection['updated_at'].isoformat():
        return render(request,'inventory/application_connection.html',{'stale':True,'reload_url':reload_url},status=409)
    data=analyze(request,cached_map,snapshot,request.GET.get('q','')[:255],request.GET.get('scope','')[:255],
        request.GET.get('service','')[:255],request.GET.get('disabled')=='1',request.GET.get('hide','')[:255].split(','))
    edge=next((e for e in data['applications']['edges'] if e['source']['name']==request.GET.get('source') and e['destination']['name']==request.GET.get('destination')),None)
    if edge is None: raise Http404('Connection not in this application view')
    # A cache eviction can rebuild the same map; changed evidence must not replace it.
    if data['map_source_stamp']!=request.GET['stamp']:
        return render(request,'inventory/application_connection.html',{'stale':True,'reload_url':reload_url},status=409)
    paths=edge['rule_paths']
    wanted=set(paths)
    stored={r.get('path'):r for r in load(snapshot,'rules')['rules'] if r.get('path') in wanted}
    current_stamp=SnapshotAnalysisIndex.objects.filter(snapshot=snapshot,version=VERSION).values_list('updated_at',flat=True).first()
    if current_stamp is None or current_stamp.isoformat()!=request.GET['stamp']:
        return render(request,'inventory/application_connection.html',{'stale':True,'reload_url':reload_url},status=409)
    params={key:request.GET[key] for key in ('snapshot','q','service','disabled','hide') if key in request.GET}
    params['tab']='map'
    rule_base=reverse('segmentation',args=[pk])+'?'+urlencode(params)+'&rule='
    rules=[]
    for path in paths:
        if path not in stored: continue
        item=stored[path];rule=item['configuration'].get('rule') or {}
        rules.append({'name':item['name'],'action':rule.get('action',item.get('action','Unknown')),
                      'disabled':bool(rule.get('disabled',item.get('disabled'))),'url':rule_base+quote_plus(path)})
    return render(request,'inventory/application_connection.html',{'rules':rules,'rule_count':edge['rule_count'],
        'missing_rules':len(rules)!=len(paths),'environment':env,'snapshot':snapshot})
