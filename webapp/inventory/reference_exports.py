"""Short-lived, owner-only export jobs in an independent worker lane."""
import gzip
import hashlib
import io
import json
import logging
import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction, connection
from django.db.models import Sum
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods, require_GET
from .models import ReferenceExport, Snapshot, MembershipRelation, MembershipLookup
from .reference_export_data import validate, owner, build, MAX_ROWS
from .reference_export_formats import render, MIMES, MAX_CONTENT

LOG = logging.getLogger('inventory.exports')
MAX_ARTIFACT = 16*1024*1024
USER_QUOTA = 64*1024*1024
ACTIVE = ('queued','running')


def expire():
    now = timezone.now()
    ReferenceExport.objects.filter(expires_at__lte=now).exclude(status='expired').update(
        status='expired',artifact=None,artifact_bytes=0,observation={},error='Export expired. Prepare another export.')
    ReferenceExport.objects.filter(created_at__lt=now-timedelta(days=7),status='expired').delete()


def state(job):
    result = {'id':str(job.pk),'status':job.status,'format':job.options['format'],'section':job.options['section'],
        'created_at':job.created_at.isoformat(),'expires_at':job.expires_at.isoformat(),'error':job.error,
        'object_name':job.options.get('object_name','Object'),'filename':job.filename,'content_bytes':job.content_bytes,'status_url':reverse('reference-export',args=[job.pk])}
    if job.status=='ready': result['download_url']=reverse('reference-export-download',args=[job.pk])
    return result


def frozen_observation(snapshot, record):
    from .current_membership import subject,cached,namespace,digest
    subject_value=subject(snapshot,'vm',record.ordinal)
    # Keep a concurrent cache publication from mixing its metadata and edges.
    MembershipLookup.objects.select_for_update().filter(environment=snapshot.environment,namespace=namespace(snapshot.environment),
        kind='vm',key=digest(subject_value['identity'])).first()
    result=cached(snapshot,'vm',subject_value,include_items=False)
    if result['status']=='not_collected': raise ValueError('No current observation is cached. Export saved evidence or check current NSX separately first.')
    values=MembershipRelation.objects.filter(environment=snapshot.environment,namespace=namespace(snapshot.environment),
        vm_key=digest(subject_value['identity']),present=True).order_by('pk').values_list('group','checked_at')[:MAX_ROWS+1]
    items=[{**group,'observed_at':stamp.isoformat()} for group,stamp in values]
    if len(items)>MAX_ROWS: raise ValueError('Cached observation exceeds the export limit.')
    if len(json.dumps(items).encode('utf-8'))>MAX_ARTIFACT: raise ValueError('Cached observation exceeds the export limit.')
    return {key:result.get(key) for key in ('status','checked_at','stale','notes')} | {'items':items}


@login_required
@require_http_methods(['GET','POST'])
def collection(request, pk):
    snapshot=get_object_or_404(Snapshot.objects.select_related('environment').defer('report','html'),pk=pk)
    if request.method=='GET':
        expire()
        jobs=ReferenceExport.objects.filter(snapshot=snapshot,requested_by=request.user).defer('artifact','observation').order_by('-created_at')[:10]
        response=JsonResponse({'items':[state(job) for job in jobs]});response['Cache-Control']='private, no-store';return response
    try:
        expire()
        if len(request.body)>12000: raise ValueError('Invalid export request.')
        values=json.loads(request.body)
        options=validate(snapshot,values)
        request_key=uuid.UUID(str(values.get('request_key','')))
        with transaction.atomic():
            # Serializes per-user quota/idempotency checks and completion writes.
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if connection.vendor=='postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_xact_lock(%s)',[781249319])  # Export admission only; never a worker lease.
            previous=ReferenceExport.objects.filter(requested_by=request.user,request_key=request_key).defer('artifact','observation').first()
            if previous:
                if previous.snapshot_id!=snapshot.pk or previous.options!=options: raise ValueError('Request identifier was already used for another export.')
                return JsonResponse(state(previous))
            if snapshot.browsing_status!='ready': return JsonResponse({'error':'Snapshot browsing must be ready before exporting references.'},status=409)
            jobs=ReferenceExport.objects.filter(requested_by=request.user,expires_at__gt=timezone.now())
            if jobs.filter(status__in=ACTIVE).count()>=3 or ReferenceExport.objects.filter(status__in=ACTIVE).count()>=100:
                return JsonResponse({'error':'Export queue is busy. Wait for an export to finish.'},status=429)
            if jobs.filter(created_at__gte=timezone.now()-timedelta(days=1)).count()>=20:
                return JsonResponse({'error':'Daily export limit reached. Retry tomorrow.'},status=429)
            if (jobs.aggregate(size=Sum('artifact_bytes'))['size'] or 0)>=USER_QUOTA:
                return JsonResponse({'error':'Export storage limit reached. Existing downloads expire within 24 hours.'},status=429)
            record=owner(snapshot,options)
            observation=frozen_observation(snapshot,record) if options['source']=='current_cache' else {}
            job=ReferenceExport.objects.create(snapshot=snapshot,requested_by=request.user,request_key=request_key,options=options,
                observation=observation,expires_at=timezone.now()+timedelta(hours=24))
            from .audit_events import record as audit
            audit('reference_export.requested','reference_export',job.pk,actor=request.user.pk,details={'snapshot_id':str(snapshot.pk),'format':options['format'],'source':options['source']})
        return JsonResponse(state(job),status=202)
    except (ValueError,TypeError) as error:
        # Validation errors contain no database or NSX response content.
        message=str(error) if isinstance(error,ValueError) and not isinstance(error,json.JSONDecodeError) else 'Invalid export request.'
        return JsonResponse({'error':message},status=400)


@login_required
@require_http_methods(['GET','POST'])
def detail(request, pk):
    job=get_object_or_404(ReferenceExport.objects.defer('artifact','observation'),pk=pk,requested_by=request.user)
    if job.expires_at<=timezone.now():
        ReferenceExport.objects.filter(pk=pk).update(status='expired',artifact=None,artifact_bytes=0,observation={})
        job.status='expired'
    if request.method=='POST':
        ReferenceExport.objects.filter(pk=pk,status__in=ACTIVE).update(status='cancelled',finished_at=timezone.now(),observation={})
        job.refresh_from_db(fields=['status','finished_at'])
    response=JsonResponse(state(job));response['Cache-Control']='private, no-store';return response


@login_required
@require_GET
def download(request, pk):
    job=get_object_or_404(ReferenceExport,pk=pk,requested_by=request.user)
    if job.expires_at<=timezone.now():
        ReferenceExport.objects.filter(pk=pk).update(status='expired',artifact=None,artifact_bytes=0,observation={})
        return JsonResponse({'error':'Export expired. Prepare another export.'},status=410)
    if job.status!='ready' or not job.artifact: return JsonResponse({'error':'Export is not ready.'},status=409)
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(bytes(job.artifact))) as stream: content=stream.read(MAX_CONTENT+1)
    except (OSError,EOFError):
        return JsonResponse({'error':'Export could not be verified. Prepare another export.'},status=409)
    if len(content)!=job.content_bytes or len(content)>MAX_CONTENT or hashlib.sha256(content).hexdigest()!=job.checksum:
        return JsonResponse({'error':'Export could not be verified. Prepare another export.'},status=409)
    from .audit_events import record as audit
    audit('reference_export.downloaded','reference_export',job.pk,actor=request.user.pk,details={'format':job.options['format']})
    response=HttpResponse(content,content_type=job.content_type)
    response['Content-Disposition']='attachment; filename="'+job.filename+'"'
    response['Content-Length']=len(content)
    response['Cache-Control']='private, no-store'
    response['X-Content-Type-Options']='nosniff'
    return response


def fail(pk, message='Export preparation was interrupted. Prepare another export.'):
    ReferenceExport.objects.filter(pk=pk,status='running').update(status='failed',finished_at=timezone.now(),error=message,observation={})


def claim():
    expire()
    with transaction.atomic():
        rows=ReferenceExport.objects.select_for_update(skip_locked=True).filter(status='queued').order_by('created_at')
        job=rows.only('pk','status','started_at').first()
        if job:
            job.status='running';job.started_at=timezone.now();job.save(update_fields=['status','started_at'])
        return job


def prepare(pk):
    try:
        with transaction.atomic():
            if connection.vendor=='postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                    cursor.execute('SET LOCAL statement_timeout = 15000')
            job=ReferenceExport.objects.select_related('snapshot__environment').defer('artifact','snapshot__report','snapshot__html').get(pk=pk,status='running')
            if job.snapshot.browsing_status!='ready': raise ValueError('Snapshot browsing is no longer ready. Retry after indexing finishes.')
            document=build(job)
        content=render(document,job.options['format'])
        packed=gzip.compress(content,mtime=0)
        if len(packed)>MAX_ARTIFACT: raise ValueError('Export exceeds the file-size limit. Export one category at a time.')
        name=slugify(document['metadata']['object']['name'])[:70] or 'object'
        filename=f"{name}-references-{str(job.snapshot_id)[:8]}.{job.options['format']}"
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=job.requested_by_id)
            used=ReferenceExport.objects.filter(requested_by_id=job.requested_by_id,expires_at__gt=timezone.now()).exclude(pk=pk).aggregate(size=Sum('artifact_bytes'))['size'] or 0
            if used+len(packed)>USER_QUOTA: raise ValueError('Export storage limit reached. Retry after existing exports expire.')
            ReferenceExport.objects.filter(pk=pk,status='running',expires_at__gt=timezone.now()).update(status='ready',artifact=packed,
                artifact_bytes=len(packed),content_bytes=len(content),checksum=hashlib.sha256(content).hexdigest(),filename=filename,
                content_type=MIMES[job.options['format']],finished_at=timezone.now(),observation={})
    except ReferenceExport.DoesNotExist: return
    except Exception as exc:
        LOG.exception('Reference export failed job=%s',pk)
        message=str(exc) if isinstance(exc,ValueError) else 'Export preparation failed. Retry or choose a smaller category.'
        fail(pk,message)
