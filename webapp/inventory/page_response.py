"""Render only status regions for automatic, read-only refresh requests."""
from time import perf_counter
from django.shortcuts import render as django_render
from django.utils.cache import patch_vary_headers

FRAGMENTS = {
    'inventory/findings.html':'inventory/finding_regions.html',
    'inventory/activity.html':'inventory/activity_region.html',
    'inventory/my_work.html':'inventory/cleanup_tasks.html',
    'inventory/comparison.html':'inventory/comparison_region.html',
    'inventory/finding_policy.html':'inventory/recalculation_region.html',
    'inventory/deletion_plan.html':'inventory/deletion_plan_region.html',
}


def status_request(request):
    return request.method=='GET' and request.headers.get('X-Requested-With')=='task-status'


def analyze(request,operation,*args,**kwargs):
    start=perf_counter()
    try:
        return operation(*args,**kwargs)
    finally:
        request.analysis_seconds=getattr(request,'analysis_seconds',0)+(perf_counter()-start)


def render(request, template_name, context=None, **kwargs):
    if status_request(request):
        template_name=FRAGMENTS.get(template_name,template_name)
    start=perf_counter()
    response=django_render(request,template_name,context,**kwargs)
    request.render_seconds=getattr(request,'render_seconds',0)+(perf_counter()-start)
    response['Cache-Control']='private, no-store'
    patch_vary_headers(response,['X-Requested-With'])
    return response
