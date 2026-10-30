"""Snapshot-pinned segmentation workspace. All mutations are local review metadata."""
import csv
import io
from datetime import date
from uuid import UUID
from django.http import Http404
from django.core.paginator import Paginator
from .preferences import table_page_size
from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .models import Environment, Snapshot, SegmentationSettings, SegmentationBoundary, SegmentationReview, SegmentationSavedView
from .audit_events import record
from .segmentation import build


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
        return redirect('segmentation', pk=env.pk) if env else render(request,'inventory/segmentation.html',{})
    env=get_object_or_404(Environment,pk=pk)
    snapshots=env.snapshots.filter(testing=False, imported=False).defer('report','html')
    latest=snapshots.first()
    if request.GET.get('snapshot'):
        try: UUID(request.GET['snapshot'])
        except ValueError: raise Http404('Invalid snapshot')
    snap=get_object_or_404(snapshots,pk=request.GET['snapshot']) if request.GET.get('snapshot') else latest
    if request.GET.get('check')=='1':
        response=JsonResponse({'latest':str(latest.pk) if latest else ''})
        response['Cache-Control']='private, no-store'
        return response
    if snap and not request.GET.get('snapshot'):
        query=request.GET.copy();query['snapshot']=str(snap.pk)
        return redirect(reverse('segmentation',args=[pk])+'?'+query.urlencode())
    settings=SegmentationSettings.objects.filter(environment=env).first()
    scope=settings.grouping_scope if settings else ''
    filters={k:request.GET.get(k,'')[:255] for k in ('q','service','tab','disabled','hide')}
    if filters['tab'] not in ('matrix','map','priorities','changes','settings'): filters['tab']='map'
    filters['groups'] = list(dict.fromkeys(request.GET.getlist('groups')))[:12]
    ready=snap and snap.browsing_status=='ready'
    data=build(snap,filters['q'],scope,filters['service'],filters['disabled']=='1',filters['hide'].split(','), selected_paths=filters['groups'] if filters['tab']=='matrix' else None, application_mode=filters['tab']=='map') if ready else {}
    if snap and (snap.needs_review or snap.summary.get('inventory_warnings')):
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
    boundaries=list(SegmentationBoundary.objects.filter(environment=env,enabled=True))
    deviations=[]
    for boundary in boundaries:
        matches=[r for r in rules.values() if not r['disabled'] and r['action']=='ALLOW' and not (r['configuration']['rule'].get('sources_excluded') or r['configuration']['rule'].get('destinations_excluded')) and (boundary.source in r['sources'] or 'ANY' in r['sources']) and (boundary.destination in r['destinations'] or 'ANY' in r['destinations'])]
        deviations.append({'boundary':boundary,'rules':matches})
    reviews={r.rule_path:r for r in SegmentationReview.objects.filter(environment=env)}
    for row in rules.values():
        review=reviews.get(row['path']);row['review']=review
        row['review_stale']=bool(review and (review.fingerprint!=row['fingerprint'] or review.state=='exception' and review.expires_at and review.expires_at<=date.today()))
        params=request.GET.copy();params['rule']=row['path'];row['url']='?'+params.urlencode()
        row['object_url']=reverse('snapshot',args=[snap.pk])+'?'+urlencode({'object':row['path']})
    for g in data.get('group_catalog',[]):
        g['url']=reverse('snapshot',args=[snap.pk])+'?'+urlencode({'object':g['path']})
    application_scopes=sorted({t['scope'] for g in data.get('group_catalog',[]) for t in g['evidence']['tags']})
    for app in data.get('applications', {}).get('nodes', []):
        for group in app['groups']:
            group['url']=reverse('snapshot',args=[snap.pk])+'?'+urlencode({'object':group['path']})
        app['matrix_url']='?'+urlencode({'snapshot':str(snap.pk),'tab':'matrix','groups':[g['path'] for g in app['groups'][:12]]},doseq=True)
    changes=[];group_changes=[];previous=None
    if ready and filters['tab']=='changes':
        previous=snapshots.filter(generated_at__lt=snap.generated_at,browsing_status='ready').first()
        if previous:
            old=build(previous,filters['q'],scope,filters['service'],filters['disabled']=='1',filters['hide'].split(','))
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
    priority_page=Paginator(data.get('priorities',[]),table_page_size(request)).get_page(request.GET.get('page'))
    return render(request,'inventory/segmentation.html',dict(application_scopes=application_scopes,priority_page=priority_page,group_changes=group_changes,environment=env,snapshot=snap,latest=latest,snapshots=snapshots[:50],ready=ready,
        scope=scope,filters=filters,tabs=tabs,selected=selected,review_form=form,deviations=deviations,saved_views=saved,changes=changes,previous=previous,**data))
