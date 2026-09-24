import json
from uuid import UUID
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.debug import sensitive_post_parameters

from .forms import EnvironmentForm, PreferencesForm
from .preferences import preferences
from .models import AuditJob, Environment, Snapshot
from .services import enqueue, prepare_snapshot, engine


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden("Staff access is required to change configuration or collect reports.")
        return view(request, *args, **kwargs)
    return wrapped


@staff_required
def administration(request):
    return render(request, "inventory/administration.html", {"environments": Environment.objects.all()})


def snapshot_list():
    return Snapshot.objects.defer("report", "html").select_related("environment")


@login_required
def dashboard(request):
    from .usability import freshness, policy
    options = policy()
    cards = []
    for environment in Environment.objects.all():
        latest = snapshot_list().filter(environment=environment).first()
        active = environment.jobs.filter(status__in=["queued", "running"]).first()
        cards.append({"environment": environment, "latest": latest, "active": active, "freshness": freshness(environment, options)})
    from django.utils import timezone
    from datetime import timedelta
    failed_count = AuditJob.objects.filter(status='failed', finished_at__gte=timezone.now()-timedelta(hours=24)).count()
    jobs = AuditJob.objects.select_related("environment", "snapshot").defer("config", "snapshot__report", "snapshot__html")[:8]
    return render(request, "inventory/dashboard.html", {
        "stale_count":sum(card['freshness']['stale'] for card in cards), "failed_count":failed_count,
        "running_count":AuditJob.objects.filter(status='running').count(), "queued_count":AuditJob.objects.filter(status='queued').count(),
        "new_issue_count":sum(card['latest'].summary.get('new_coverage_issues',0) for card in cards if card['latest']),
        "cards": cards, "jobs": jobs, "environment_count": len(cards),
        "snapshot_count": Snapshot.objects.count(),
        "active_count": AuditJob.objects.filter(status__in=["queued", "running"]).count(),
    })


@login_required
def environment_detail(request, pk):
    environment = get_object_or_404(Environment, pk=pk)
    snapshots = Paginator(snapshot_list().filter(environment=environment), preferences(request).page_size).get_page(request.GET.get("page"))
    from .usability import freshness
    return render(request, "inventory/environment.html", {
        "freshness": freshness(environment),
        "environment": environment, "snapshots": snapshots,
        "latest": snapshot_list().filter(environment=environment).first(),
        "active": environment.jobs.filter(status__in=["queued", "running"]).first(),
        "jobs": environment.jobs.select_related("environment", "snapshot").defer("config", "snapshot__report", "snapshot__html")[:10],
    })


@staff_required
@sensitive_post_parameters("password")
def environment_edit(request, pk=None):
    environment = get_object_or_404(Environment, pk=pk) if pk else None
    form = EnvironmentForm(request.POST or None, request.FILES or None, instance=environment)
    if request.method == "POST" and form.is_valid():
        # Prevent origin changes from mixing manager inventories under one history.
        with transaction.atomic():
            current = Environment.objects.select_for_update().get(pk=pk) if pk else None
            if current and current.jobs.filter(status__in=["queued", "running"]).exists():
                form.add_error(None, "Wait for the active audit to finish before editing this environment.")
            elif current and current.manager != form.cleaned_data["manager"] and current.snapshots.exists():
                form.add_error("manager", "Create a new environment for a different manager to preserve history.")
            else:
                environment = form.save()
                messages.success(request, "Environment saved.")
                return redirect("environment", pk=environment.pk)
    return render(request, "inventory/environment_form.html", {"form": form, "environment": environment})


@staff_required
@require_POST
def collect(request, pk):
    environment = get_object_or_404(Environment, pk=pk)
    try:
        enqueue(environment, request.user, testing=request.POST.get("testing") == "1")
        messages.success(request, "Audit queued. The worker will collect a new snapshot.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("environment", pk=pk)


@staff_required
def testing_data(request):
    if request.method == "POST":
        from .demo import create_demo_environment
        environment = create_demo_environment()
        messages.success(request, "Demo environment created with synthetic testing data. Automatic collection is disabled.")
        return redirect("environment", pk=environment.pk)
    return render(request, "inventory/testing_data.html")


@login_required
def snapshot_detail(request, pk):
    snapshot = get_object_or_404(snapshot_list(), pk=pk)
    request.session['selected_environment'] = snapshot.environment_id
    history = list(snapshot_list().filter(environment=snapshot.environment)[:50])
    if not any(item.pk == snapshot.pk for item in history):
        history.append(snapshot)
    # JSONField maps to PostgreSQL JSONB; render from the immutable database snapshot.
    report = engine().render_html_report(snapshot.report, workspace=True)
    response = render(request, "inventory/snapshot.html", {
        "snapshot": snapshot, "environment": snapshot.environment, "history": history, "report": report, "is_latest": bool(history and history[0].pk == snapshot.pk)})
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
    return response


@login_required
@require_GET
def report_content(request, pk):
    # Keep bookmarked legacy report links within the integrated workspace.
    get_object_or_404(Snapshot.objects.only("pk"), pk=pk)
    return redirect("snapshot", pk=pk)


@login_required
@require_GET
def api_jobs(request):
    jobs = AuditJob.objects.select_related("environment").defer("config")
    requested = request.GET.getlist("id")
    if requested:
        try:
            if len(requested) > 100:
                raise ValueError()
            jobs = jobs.filter(pk__in=[UUID(value) for value in requested])
        except ValueError:
            return JsonResponse({"error": "Provide at most 100 valid job IDs."}, status=400)
    else:
        jobs = jobs[:50]
    response = JsonResponse({"jobs": [{"id": str(job.pk), "environment": job.environment.name,
        "status": job.status, "progress": job.progress,
        "created_at": job.created_at.isoformat(), "error": job.error} for job in jobs]})
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
def health(request):
    from django.db import connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@login_required
@require_GET
def rule_history(request, pk):
    from django.utils import timezone
    from .history import analyze, STATUS_LABELS
    environment = get_object_or_404(Environment, pk=pk)
    try:
        days = int(request.GET.get("days", preferences(request).history_days))
    except ValueError:
        days = preferences(request).history_days
    if days not in (7, 30, 90):
        days = preferences(request).history_days
    end = timezone.now()
    anchor = None
    if request.GET.get("snapshot"):
        try:
            anchor = get_object_or_404(environment.snapshots.only("generated_at"), pk=UUID(request.GET["snapshot"]))
        except ValueError:
            from django.http import Http404
            raise Http404()
        end = anchor.generated_at
    result = analyze(environment, days, end)
    selected = request.GET.get("status", "")
    query = request.GET.get("q", "").strip()
    rows = result.pop("rows")
    for row in rows:
        row["label"] = STATUS_LABELS[row["status"]]
    if selected in STATUS_LABELS:
        rows = [r for r in rows if r["status"] == selected]
    if query:
        rows = [r for r in rows if query.casefold() in (r["name"]+' '+r["path"]+' '+str(r["rule_id"])).casefold()]
    rows.sort(key=lambda r: (r["name"].casefold(), r["path"]))
    params = request.GET.copy()
    params.pop("page", None)
    response = render(request, "inventory/rule_history.html", {"environment": environment, "days": days,
        "periods": (7, 30, 90), "anchor": anchor, "history": result, "query": query,
        "selected": selected, "statuses": STATUS_LABELS.items(), "params": params.urlencode(),
        "rows": Paginator(rows, preferences(request).page_size).get_page(request.GET.get("page"))})
    response["Cache-Control"] = "private, no-store"
    return response


SETTINGS_SECTIONS = {
    'appearance': ('Appearance', ['theme','density','text_size','high_contrast','reduced_motion']),
    'dates': ('Dates & time', ['timezone','date_format']),
    'tables': ('Tables & navigation', ['page_size','report_page_size','history_days','remember_tables','landing_page','preferred_environment','remember_menus']),
    'updates': ('Live updates', ['refresh_seconds']),
}


@login_required
def website_settings(request):
    from .models import UserPreferences
    section = request.GET.get('section', 'appearance')
    if section not in SETTINGS_SECTIONS:
        section = 'appearance'
    title, fields = SETTINGS_SECTIONS[section]
    instance = preferences(request)
    # Support existing clients posting the complete preferences form.
    full_post = request.method == 'POST' and 'section' not in request.GET and 'page_size' in request.POST and 'timezone' in request.POST
    form = PreferencesForm(request.POST or None, instance=instance)
    if not full_post:
        form.fields = {key: form.fields[key] for key in fields}
    if request.method == 'POST':
        if request.POST.get('action') == 'reset':
            defaults = UserPreferences()
            values = {key: getattr(defaults, key) for key in fields}
        elif form.is_valid():
            values = form.cleaned_data
        else:
            values = None
        if values is not None:
            UserPreferences.objects.update_or_create(user=request.user, defaults=values)
            messages.success(request, 'Your website settings have been saved.')
            from django.urls import reverse
            return redirect(reverse('website-settings') + ('?section='+section if not full_post else ''))
    return render(request, 'inventory/website_settings.html', {'form':form,'settings_section':section,
        'settings_title':title,'settings_sections':[(key,value[0]) for key,value in SETTINGS_SECTIONS.items()]})


@login_required
@require_GET
def collection_history(request, pk):
    environment = get_object_or_404(Environment, pk=pk)
    jobs = Paginator(environment.jobs.select_related("environment", "snapshot").defer("config", "snapshot__report", "snapshot__html"),
                     preferences(request).page_size).get_page(request.GET.get("page"))
    return render(request, "inventory/collection_history.html", {"environment": environment, "jobs": jobs})


@login_required
def retention_settings(request):
    from .models import RetentionPolicy
    from .forms import RetentionForm
    from .retention import preview
    if not request.user.is_superuser:
        return HttpResponseForbidden('Administrator access is required to change data retention.')
    policy = RetentionPolicy.objects.filter(pk=1).first() or RetentionPolicy(pk=1)
    form = RetentionForm(request.POST or None, instance=policy)
    if request.method == 'POST' and form.is_valid():
        policy = form.save(commit=False)
        if request.POST.get('action') == 'save':
            # Serialize with cleanup; form changes do not overwrite the last-run metadata.
            with transaction.atomic():
                RetentionPolicy.objects.get_or_create(pk=1)
                current = RetentionPolicy.objects.select_for_update().get(pk=1)
                for field, value in form.cleaned_data.items():
                    setattr(current, field, value)
                current.save(update_fields=list(form.cleaned_data))
            messages.success(request, 'Retention policy saved. Cleanup runs hourly when enabled.')
            return redirect('retention-settings')
    rows = preview(policy) if request.method != 'POST' or form.is_valid() else []
    return render(request, 'inventory/retention_settings.html', {'form': form, 'policy': policy,
        'preview_rows': rows, 'proposed': request.method == 'POST'})


@login_required
def landing(request):
    saved = preferences(request)
    if saved.preferred_environment_id and saved.landing_page in ('environment', 'activity'):
        return redirect('environment' if saved.landing_page == 'environment' else 'rule-history', pk=saved.preferred_environment_id)
    return redirect('dashboard')


@login_required
def workspace_policy(request):
    from .forms import WorkspacePolicyForm
    from .models import WorkspacePolicy
    from .usability import policy
    if not request.user.is_superuser:
        return HttpResponseForbidden('Administrator access is required.')
    form = WorkspacePolicyForm(request.POST or None, instance=policy())
    if request.method == 'POST' and form.is_valid():
        WorkspacePolicy.objects.update_or_create(pk=1, defaults=form.cleaned_data)
        messages.success(request, 'Collection and notification policy saved.')
        return redirect('workspace-policy')
    return render(request, 'inventory/workspace_policy.html', {'form': form})


@login_required
@require_POST
def interface_preferences(request):
    from .models import UserPreferences
    # Small, validated per-table records. Arbitrary user IDs and redirects are never accepted.
    if len(request.body) > 32768:
        return JsonResponse({'error': 'Settings are too large.'}, status=400)
    try:
        data = json.loads(request.body)
        key, value = data['key'], data['value']
        if not isinstance(key, str) or len(key) > 160 or not key.startswith(('table:', 'menu:')):
            raise ValueError()
        if key.startswith('menu:'):
            if type(value) is not bool:
                raise ValueError()
        else:
            if not isinstance(value, dict) or set(value) - {'controls', 'filters', 'columns'}:
                raise ValueError()
            controls = value.get('controls', {})
            if not isinstance(controls, dict) or len(controls) > 10 or any(not isinstance(k, str) or not isinstance(v, (str, bool)) or len(str(v)) > 2000 for k,v in controls.items()):
                raise ValueError()
            filters = value.get('filters', [])
            if not isinstance(filters, list) or len(filters) > 30:
                raise ValueError()
            for index, condition in filters:
                if type(index) is not int or not 0 <= index < 30 or not isinstance(condition, dict) or set(condition) != {'text', 'mode'} or condition['mode'] not in ('contains', 'excludes') or not isinstance(condition['text'], str) or len(condition['text']) > 2000:
                    raise ValueError()
            columns = value.get('columns', [])
            if not isinstance(columns, list) or len(columns) > 30:
                raise ValueError()
            seen = set()
            for column in columns:
                if not isinstance(column, dict) or set(column) != {'index', 'visible'} or type(column['index']) is not int or not 0 <= column['index'] < 30 or type(column['visible']) is not bool or column['index'] in seen:
                    raise ValueError()
                seen.add(column['index'])
    except (ValueError, TypeError, KeyError):
        return JsonResponse({'error': 'Invalid interface preferences.'}, status=400)
    with transaction.atomic():
        UserPreferences.objects.get_or_create(user=request.user)
        saved = UserPreferences.objects.select_for_update().get(user=request.user)
        if not (saved.remember_tables if key.startswith('table:') else saved.remember_menus):
            return JsonResponse({'saved': False})
        state = dict(saved.interface_state)
        state.pop(key, None)
        state[key] = value
        # Bound storage, retaining the most recently changed sections.
        saved.interface_state = dict(list(state.items())[-150:])
        saved.save(update_fields=['interface_state'])
    return JsonResponse({'saved': True})


@login_required
@require_GET
def notifications(request):
    from django.db.models import Q
    from django.utils import timezone
    from .usability import policy
    from datetime import timedelta
    options = policy()
    condition = Q(pk__in=[])
    if options.notify_failed:
        condition |= Q(status='failed')
    if options.notify_completed:
        condition |= Q(status='succeeded')
    if options.notify_coverage:
        condition |= Q(status='succeeded', snapshot__summary__new_coverage_issues__gt=0)
    jobs = AuditJob.objects.filter(condition, finished_at__gte=timezone.now()-timedelta(days=30)).select_related('environment', 'snapshot').defer('config','error','snapshot__report','snapshot__html')
    seen = preferences(request).notifications_seen_at
    unread = jobs.filter(finished_at__gt=seen).count() if seen else jobs.count()
    items = []
    from django.urls import reverse
    for job in jobs.order_by('-finished_at')[:50]:
        snapshot = getattr(job, 'snapshot', None)
        issues = snapshot.summary.get('new_coverage_issues', 0) if snapshot else 0
        label = 'Collection failed' if job.status == 'failed' else 'Audit completed'
        if job.testing:
            label = 'Testing collection failed' if job.status == 'failed' else 'Testing audit completed'
        if options.notify_coverage and issues:
            label += f' · {issues} new coverage issue(s)'
        items.append({'id': str(job.pk), 'label': label, 'environment': job.environment.name, 'at': job.finished_at.isoformat(),
                      'unread': not seen or job.finished_at > seen,
                      'url': reverse('snapshot', args=[snapshot.pk]) if snapshot else reverse('collection-history', args=[job.environment_id])})
    return JsonResponse({'items': items, 'unread': unread})


@login_required
@require_POST
def notifications_read(request):
    from .models import UserPreferences
    from django.utils import timezone
    UserPreferences.objects.update_or_create(user=request.user, defaults={'notifications_seen_at': timezone.now()})
    return JsonResponse({'saved': True})


@login_required
@require_GET
def workspace_section(request, section):
    from django.urls import reverse
    sections = {'inventory':'overview','services':'all-services','tags':'tags-all','scopes':'tags-scopes',
                'firewall':'dfw-overview','policies':'dfw-policies','rules':'dfw-rules','help':'feature-guide'}
    environments = Environment.objects.all()
    selected = request.GET.get('environment') or request.session.get('selected_environment') or preferences(request).preferred_environment_id
    try:
        environment = environments.filter(pk=int(selected)).first() if selected else None
    except (ValueError, TypeError):
        environment = None
    environment = environment or environments.first()
    if environment:
        request.session['selected_environment'] = environment.pk
        if section == 'collections':
            return redirect('collection-history', pk=environment.pk)
        if section == 'activity':
            return redirect('rule-history', pk=environment.pk)
        if section == 'environment':
            return redirect('environment', pk=environment.pk)
        snapshot = snapshot_list().filter(environment=environment).first()
        if snapshot and section in sections:
            allowed = set(sections.values()) | {'overview','all-groups','unused-groups','empty-groups','unknown-membership','unused-services','empty-policies','zero-hit-rules','disabled-rules','unknown-statistics','empty-group-rules','dfw-scope-rules','tags-both','tags-vm_only','tags-group_only','tags-other_only','tags-unknown','coverage','tags-coverage'}
            panel = request.GET.get('panel')
            return redirect(reverse('snapshot',args=[snapshot.pk])+'#'+(panel if panel in allowed else sections[section]))
    return render(request, 'inventory/section_empty.html', {'environment':environment,'section_name':section.title()})


@login_required
@require_GET
def environment_directory(request):
    from .usability import freshness, policy
    query = request.GET.get('q','').strip()
    environments = Environment.objects.all()
    if query:
        from django.db.models import Q
        environments = environments.filter(Q(name__icontains=query)|Q(manager__icontains=query))
    options=policy()
    page=Paginator(environments, preferences(request).page_size).get_page(request.GET.get('page'))
    cards=[{'environment':env,'latest':snapshot_list().filter(environment=env).first(),
            'freshness':freshness(env,options),'active':env.jobs.filter(status__in=['queued','running']).first()} for env in page]
    return render(request,'inventory/environment_directory.html',{'cards':cards,'page':page,'query':query,'view':request.GET.get('view','table')})


@login_required
@require_GET
def all_collections(request):
    jobs = AuditJob.objects.select_related('environment','snapshot').defer('config','snapshot__report','snapshot__html')
    state=request.GET.get('status','')
    if state in AuditJob.Status.values:
        jobs=jobs.filter(status=state)
    page=Paginator(jobs,preferences(request).page_size).get_page(request.GET.get('page'))
    return render(request,'inventory/all_collections.html',{'jobs':page,'page':page,'selected_status':state,'statuses':AuditJob.Status.choices})
