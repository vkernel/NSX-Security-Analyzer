from datetime import timedelta
from django.views.decorators.debug import sensitive_post_parameters
from django.db import transaction, IntegrityError
from django.contrib import messages
from django import forms
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from .models import IPFIXExporter, IPFIXReceiver, Environment
from .views import staff_required


class ExporterForm(forms.ModelForm):
    class Meta:
        model = IPFIXExporter
        fields = ['environment', 'address', 'enabled']

    def clean(self):
        data = super().clean()
        if data.get('enabled') and IPFIXExporter.objects.filter(enabled=True).count() >= 1000:
            raise forms.ValidationError('The pilot supports at most 1,000 enabled exporter mappings.')
        return data


@staff_required
@require_http_methods(['GET', 'POST'])
def settings(request):
    sources_page = request.resolver_match.url_name == 'ipfix-sources'
    target = 'ipfix-sources' if sources_page else 'ipfix-settings'
    receiver, _ = IPFIXReceiver.objects.get_or_create(pk=1)
    form = ExporterForm()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'toggle':
            receiver.enabled = request.POST.get('enabled') == 'on'
            receiver.save(update_fields=['enabled'])
            return redirect(target)
        if action == 'delete':
            try:
                exporter_id = int(request.POST.get('exporter', ''))
            except (ValueError, TypeError):
                raise Http404('Exporter not found')
            get_object_or_404(IPFIXExporter, pk=exporter_id).delete()
            return redirect(target)
        if action == 'add':
            form = ExporterForm(request.POST)
            if form.is_valid():
                form.save()
                return redirect(target)
    online = receiver.heartbeat and receiver.heartbeat > timezone.now() - timedelta(seconds=30)
    return render(request, 'inventory/ipfix_sources.html' if sources_page else 'inventory/ipfix.html', {'receiver': receiver, 'online': online,
        'diagnostics': [d for d in reversed(receiver.diagnostics) if d.get('last_seen', '') > (timezone.now()-timedelta(hours=1)).isoformat()],
        'form': form, 'exporters': IPFIXExporter.objects.select_related('environment').all()})


class VCenterForm(forms.Form):
    environment = forms.ModelChoiceField(queryset=Environment.objects.all())
    server = forms.CharField(label='vCenter hostname', max_length=255)
    username = forms.CharField(max_length=255)
    password = forms.CharField(widget=forms.PasswordInput, strip=False)
    def clean_server(self):
        from .ipfix.vcenter import origin, DiscoveryError
        try:
            origin(self.cleaned_data['server'])
        except DiscoveryError as exc:
            raise forms.ValidationError(str(exc))
        return self.cleaned_data['server']



@staff_required
@sensitive_post_parameters('password')
@require_http_methods(['GET', 'POST'])
def vcenter_discovery(request):
    import time
    from .ipfix.vcenter import discover, DiscoveryError
    form = VCenterForm()
    ca_preview = request.session.get('vcenter_ca_preview')
    if ca_preview and time.time() - ca_preview['created'] > 600:
        request.session.pop('vcenter_ca_preview', None)
        ca_preview = None
    if request.method == 'POST' and request.POST.get('action') == 'retrieve_ca':
        from .ipfix.certificates import retrieve
        from .ipfix.vcenter import origin
        request.session.pop('vcenter_ca_preview', None)
        ca_preview = None
        server = request.POST.get('server', '').strip()
        try:
            if len(server) > 255:
                raise DiscoveryError('Enter a vCenter hostname of at most 255 characters.')
            ca_preview = dict(retrieve(server), server=server, origin=list(origin(server)), created=time.time())
            request.session['vcenter_ca_preview'] = ca_preview
            form = VCenterForm(initial={'server': server})
        except DiscoveryError as exc:
            messages.error(request, str(exc))
    preview = request.session.get('ipfix_discovery')
    if preview and time.time() - preview['created'] > 600:
        request.session.pop('ipfix_discovery', None)
        preview = None
    if request.method == 'POST' and request.POST.get('action') == 'discover':
        request.session.pop('ipfix_discovery', None)
        preview = None
        form = VCenterForm(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data
            try:
                ca_data = None
                if request.POST.get('trust_retrieved') == 'on':
                    from .ipfix.vcenter import origin
                    if not ca_preview or list(origin(data['server'])) != ca_preview['origin']:
                        raise DiscoveryError('Retrieved certificates expired or belong to another vCenter. Retrieve them again.')
                    ca_data = ca_preview['pem']
                rows, issues = discover(data['server'], data['username'], data['password'], ca_data)
                preview = {'created': time.time(), 'environment': data['environment'].pk,
                           'environment_name': data['environment'].name, 'rows': rows, 'issues': issues}
                # Store only the preview, never credentials or the CA upload.
                request.session['ipfix_discovery'] = preview
                form = VCenterForm()
                request.session.pop('vcenter_ca_preview', None)
                ca_preview = None
            except DiscoveryError as exc:
                form.add_error(None, str(exc))
    elif request.method == 'POST' and request.POST.get('action') == 'apply':
        try:
            if not preview:
                raise ValueError('Discovery preview expired. Connect again.')
            selected = set(request.POST.getlist('selected'))
            valid = {str(i): row for i, row in enumerate(preview['rows'])}
            if not selected or not selected <= valid.keys():
                raise ValueError('Select hosts from the current discovery preview.')
            addresses = sorted({valid[i]['address'] for i in selected})
            with transaction.atomic():
                IPFIXReceiver.objects.get_or_create(pk=1)
                IPFIXReceiver.objects.select_for_update().get(pk=1)
                environment = Environment.objects.filter(pk=preview['environment']).first()
                if environment is None:
                    raise ValueError('The selected environment no longer exists.')
                existing = {e.address: e for e in IPFIXExporter.objects.filter(address__in=addresses)}
                if any(e.environment_id != environment.pk for e in existing.values()):
                    raise ValueError('A selected address belongs to another environment. No mappings were changed.')
                new = [a for a in addresses if a not in existing]
                if IPFIXExporter.objects.filter(enabled=True).count() + len(new) > 1000:
                    raise ValueError('The selection exceeds the 1,000 enabled exporter limit.')
                IPFIXExporter.objects.bulk_create([IPFIXExporter(environment=environment, address=a) for a in new])
            request.session.pop('ipfix_discovery', None)
            messages.success(request, f'Added {len(new)} exporter mappings; {len(existing)} existing mappings unchanged. Reception settings were not changed.')
            return redirect('ipfix-settings')
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc) if isinstance(exc, ValueError) else 'Mappings changed concurrently. Refresh discovery and try again.')
    return render(request, 'inventory/vcenter_discovery.html', {'form': form, 'preview': preview, 'ca_preview': ca_preview})
