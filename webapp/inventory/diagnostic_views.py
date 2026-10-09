import json
from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from .audit_events import record
from .models import AuditEvent, AuditJob, Environment
from .services import enqueue


@login_required
@require_GET
def audit_log(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden('Administrator access required.')
    rows = AuditEvent.objects.all()
    query = request.GET.get('q', '').strip()[:200]
    outcome = request.GET.get('outcome', '')
    if query:
        rows = rows.filter(Q(action__icontains=query) | Q(target_id__icontains=query) |
                           Q(actor_id_text__icontains=query) | Q(request_id__icontains=query))
    if outcome in ('success', 'failed', 'denied', 'processed'):
        rows = rows.filter(outcome=outcome)
    if request.GET.get('export') == 'json':
        # Bounded export with the current filters. No model or secret serialization.
        data = list(rows.values('id', 'created_at', 'action', 'outcome', 'actor_id_text',
                               'target_type', 'target_id', 'request_id', 'details')[:5000])
        record('audit.exported', details={'count': len(data), 'limit': 5000})
        response = HttpResponse(json.dumps(data, default=str, indent=2), content_type='application/json')
        response['Content-Disposition'] = 'attachment; filename="nsx-audit-events.json"'
    else:
        params = request.GET.copy()
        params.pop('page', None)
        params.pop('export', None)
        response = render(request, 'inventory/audit_log.html', {
            'page': Paginator(rows, 50).get_page(request.GET.get('page')), 'query': query,
            'outcome': outcome, 'query_string': params.urlencode(),
            'retention_days': settings.AUDIT_EVENT_RETENTION_DAYS,
            'environments': Environment.objects.filter(enabled=True),
        })
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def collection_diagnostics(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden('Staff access required.')
    job = get_object_or_404(AuditJob.objects.defer('config','environment__password_ciphertext','environment__ca_certificate').select_related('environment'), pk=pk)
    from .models import Snapshot
    diagnostics = Snapshot.objects.filter(job_id=job.pk).values('report__performance', 'report__dfw__collection_diagnostics').first()
    response = render(request, 'inventory/collection_diagnostics.html', {
        'job': job, 'timeline': job.diagnostics.get('timeline', []),
        'error_detail': json.dumps(job.diagnostics.get('error', {}), indent=2),
        'request_statistics': json.dumps(diagnostics, indent=2) if diagnostics else '',
    })
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_POST
def diagnostic_collection(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden('Administrator access required.')
    env = get_object_or_404(Environment, pk=request.POST.get('environment'))
    try:
        job = enqueue(env, request.user, debug_until=timezone.now()+timedelta(minutes=15))
        record('collection.diagnostic_requested', 'AuditJob', job.pk,
               details={'environment_id': env.pk, 'debug_until': job.debug_until.isoformat()})
        return redirect('collection-diagnostics', pk=job.pk)
    except ValidationError:
        messages.error(request, 'The environment is paused or already has a queued/running collection.')
        record('collection.diagnostic_rejected', 'Environment', env.pk, outcome='failed')
        return redirect('audit-log')
