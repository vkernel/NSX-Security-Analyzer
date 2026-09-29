from django.db import transaction
from .ipfix.readiness import inspect_readiness
import ipaddress
import json
from django import forms
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .views import staff_required
from .models import Environment, IPFIXSetup, IPFIXExporter, IPFIXReceiver
from .ipfix.setup import client_for, make_plan, apply_plan, SetupError, PROFILES


class SetupForm(forms.Form):
    environment = forms.ModelChoiceField(queryset=Environment.objects.all())
    destination = forms.GenericIPAddressField(protocol='IPv4', label='Receiver destination IP',
        help_text='Reachable Docker host IP. This is the destination configured in NSX, not an ESXi source address.')
    port = forms.IntegerField(min_value=1,max_value=65535,initial=2055,label='Published UDP port',
        help_text='Must match the host UDP port published by your IPFIX container or forwarding rule.')
    profile_mode = forms.ChoiceField(choices=[('existing','Use existing profile'),('new','Create new profile')],initial='existing',required=False,label='Profile configuration')
    existing_profile = forms.ChoiceField(choices=[('','Select environment and load profiles')],required=False,label='Existing DFW profile')
    profile_name = forms.CharField(max_length=255,initial='NSX Security Analyzer',required=False,label='New profile name')
    priority = forms.IntegerField(min_value=0,max_value=32000,initial=100,required=False,label='New profile priority',help_text='Lower numbers take precedence on overlapping scope. Creating a profile does not disable existing profiles.')

    def __init__(self,*args,profiles=(),**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['existing_profile'].choices=[('','Select a profile')]+[(p['path'],p.get('display_name',p['path'])+' · priority '+str(p.get('priority',0))) for p in profiles]

    def clean(self):
        data=super().clean()
        data['profile_mode']=data.get('profile_mode') or 'existing'
        data['profile_name']=data.get('profile_name') or 'NSX Security Analyzer'
        data['priority']=data.get('priority') if data.get('priority') is not None else 100
        if data['profile_mode']=='new' and not data.get('group'):
            self.add_error('group','Choose a group to activate the new profile.')
        return data

    group = forms.RegexField(regex=r'^/infra/domains/[^/]+/groups/[^/]+$',required=False,label='Group Policy path for a new export profile',
        help_text='Required for a new profile. For an existing profile, leave blank to retain its scope or provide a group path to add a binding. IPFIX group scope must contain eligible segments or segment ports.')

    def clean_destination(self):
        value=self.cleaned_data['destination']
        address=ipaddress.ip_address(value)
        if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
            raise forms.ValidationError('Enter a reachable unicast receiver address.')
        return value



@staff_required
@require_http_methods(['GET','POST'])
def setup(request):
    initial={}
    env=request.POST.get('environment') or request.GET.get('environment')
    if env and env.isdigit():
        initial={'environment':env}
    profiles=[]
    load_error=None
    if env and env.isdigit():
        environment=Environment.objects.filter(pk=env).first()
        api=None
        if environment:
            try:
                api=client_for(environment)
                profiles=list(api.items(PROFILES))
            except Exception:
                load_error='Could not load profiles. Check the environment connection and certificate trust.'
            finally:
                if api: api.http.clear()
    loading=request.method=='POST' and request.POST.get('action')=='load_profiles'
    if loading:
        initial.update({k:v for k,v in request.POST.items() if k not in ('csrfmiddlewaretoken','action')})
    form=SetupForm(None if loading else request.POST or None,initial=initial,profiles=profiles)
    if load_error:
        messages.error(request,load_error)
    if request.method=='POST' and not loading and form.is_valid():
        client=None
        try:
            values=form.cleaned_data
            client=client_for(values['environment'])
            plan=make_plan(client,**values)
            plan['readiness']=inspect_readiness(client,plan)
            plan['readiness_checked_at']=timezone.now().isoformat()
            record=IPFIXSetup.objects.create(environment=values['environment'],actor=request.user,plan=plan)
            return redirect('ipfix-setup-review',pk=record.pk)
        except Exception as exc:
            form.add_error(None,str(exc) if isinstance(exc,SetupError) else 'Could not inspect NSX configuration. Check connectivity, certificate trust and read permissions.')
        finally:
            if client: client.http.clear()
    return render(request,'inventory/ipfix_setup.html',{'form':form,'recent':IPFIXSetup.objects.select_related('environment').order_by('-pk')[:20]})


@staff_required
@require_http_methods(['GET','POST'])
def review(request,pk):
    record=get_object_or_404(IPFIXSetup,pk=pk,actor=request.user)
    if request.method=='POST':
        if request.POST.get('action') == 'readiness':
            client=None
            try:
                client=client_for(record.environment)
                evidence=inspect_readiness(client,record.plan)
                with transaction.atomic():
                    current=IPFIXSetup.objects.select_for_update().get(pk=record.pk)
                    current.plan['readiness']=evidence
                    current.plan['readiness_checked_at']=timezone.now().isoformat()
                    current.save(update_fields=['plan'])
            except Exception:
                messages.error(request,'Could not refresh NSX checks. Check connectivity and certificate trust.')
            finally:
                if client: client.http.clear()
            return redirect('ipfix-setup-review',pk=record.pk)
        if request.POST.get('approve') != 'on':
            messages.error(request,'Confirm the listed NSX changes before applying.')
        else:
            try:
                apply_plan(record.pk,request.user)
            except SetupError as exc:
                messages.error(request,str(exc))
            return redirect('ipfix-setup-review',pk=record.pk)
    record.refresh_from_db()
    exporters=IPFIXExporter.objects.filter(environment=record.environment,enabled=True).exclude(address=record.plan['destination'])
    received=bool(record.applied_at and any(e.last_received and e.last_received >= record.applied_at and e.messages-e.malformed > record.plan.get('baseline',{}).get(e.address,0) for e in exporters))
    operations=[dict(op,display=json.dumps(op['body'],indent=2),before_display=json.dumps(op['before'],indent=2)) for op in record.plan['operations']]
    receiver=IPFIXReceiver.objects.filter(pk=1).first()
    return render(request,'inventory/ipfix_setup_review.html',{'setup':record,'operations':operations,'received':received,'exporters':exporters,'receiver':receiver,'poll':record.status=='applied' and not received and (timezone.now()-record.applied_at).total_seconds()<180})
