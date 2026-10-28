from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from .models import Snapshot


@login_required
@require_POST
def restore_browsing(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden('Administrator access required to rebuild archived browsing data.')
    with transaction.atomic():
        snapshot = get_object_or_404(Snapshot.objects.select_for_update(), pk=pk)
        if snapshot.browsing_status in ('archived', 'failed'):
            snapshot.browsing_status = 'queued'
            snapshot.save(update_fields=['browsing_status'])
    return redirect('snapshot', pk=pk)
