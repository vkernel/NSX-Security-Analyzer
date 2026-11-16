"""Get started checklist; each step's state is read from the workspace, never assumed."""
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.views.decorators.http import require_GET

from .models import AuditJob, Environment, Finding, NamingPolicy, SegmentationSettings
from .page_response import render


def steps(user):
    environment = Environment.objects.only('pk').first()
    collected = AuditJob.objects.filter(status='succeeded', testing=False).exists()
    eligible = Finding.objects.filter(present=True, qualification='eligible').exists()
    reviewed = Finding.objects.filter(owner__isnull=False).exists()
    scoped = SegmentationSettings.objects.exclude(grouping_scope='').exists()
    naming = NamingPolicy.objects.filter(enabled=True, removed_at__isnull=True).exists()
    env_url = reverse('environment', args=[environment.pk]) if environment else None
    result = [
        {'title': 'Connect an NSX Manager', 'text': 'Add an environment with a read-only account. Credentials are encrypted and never shown again.',
         'done': environment is not None, 'action': 'Add environment' if user.is_staff else '', 'url': reverse('environment-new') if user.is_staff else ''},
        {'title': 'Collect a first snapshot', 'text': 'A collection reads groups, services, rules, VMs and tags. It only reads from NSX.',
         'done': collected, 'action': 'Open environment' if env_url else '', 'url': env_url or ''},
        {'title': 'Organise applications', 'text': 'Choose the tag scope that names your applications, so Segmentation can group them.',
         'done': scoped, 'action': 'Configure scope' if env_url else '',
         'url': reverse('segmentation', args=[environment.pk]) + '?tab=settings' if environment else ''},
        {'title': 'Set a naming policy', 'text': 'Define how group names are built; compliance is checked against the active policy.',
         'done': naming, 'action': 'Naming policies' if user.is_superuser else '', 'url': reverse('naming-policies') if user.is_superuser else ''},
        {'title': 'Review findings', 'text': 'Findings become reviewable after enough snapshots have been observed. Assign one to start a review.',
         'done': reviewed, 'action': 'Open reviews' if eligible else '', 'url': reverse('my-work') + '?queue=unassigned' if eligible else ''},
    ]
    current = next((index for index, step in enumerate(result) if not step['done']), None)
    for index, step in enumerate(result):
        step['number'] = index + 1
        step['state'] = 'done' if step['done'] else 'current' if index == current else 'next'
    return result


@login_required
@require_GET
def getting_started(request):
    items = steps(request.user)
    return render(request, 'inventory/getting_started.html', {'steps': items, 'complete': sum(step['done'] for step in items)})
