"""The web adapter calls the existing read-only collector without changing its CLI."""
import base64
import importlib.util
import json
import os
from datetime import datetime, timedelta
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AuditJob, Environment, Snapshot, manager_origin
from .credentials import decrypt_password
from .concurrency import adapt_requests


@lru_cache(maxsize=1)
def engine():
    spec = importlib.util.spec_from_file_location("nsx_audit_engine", settings.AUDIT_ENGINE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def enqueue(environment, user, testing=False):
    with transaction.atomic():
        environment = Environment.objects.select_for_update().get(pk=environment.pk)
        if not environment.enabled:
            raise ValidationError("This environment is paused. Enable it before collecting.")
        if environment.jobs.filter(status__in=["queued", "running"]).exists():
            raise ValidationError("An audit is already queued or running for this environment.")
        return AuditJob.objects.create(environment=environment, requested_by=user,
                                       testing=testing, config=environment.collection_config())


def schedule_due():
    """Persist schedules and serialize with manual collection and environment edits."""
    now = timezone.now()
    count = 0
    from django.db.models import Q
    candidates = Environment.objects.filter(enabled=True, sync_interval_minutes__gt=0).filter(
        Q(next_sync_at__lte=now) | Q(next_sync_at__isnull=True)).values_list("pk", flat=True)
    for pk in list(candidates):
        with transaction.atomic():
            environment = Environment.objects.select_for_update().get(pk=pk)
            if not environment.enabled or not environment.sync_interval_minutes:
                continue
            if environment.next_sync_at and environment.next_sync_at > now:
                continue
            # Initialize upgraded installations without launching an immediate audit.
            if environment.next_sync_at is not None:
                if environment.jobs.filter(status__in=["queued", "running"]).exists():
                    continue
                AuditJob.objects.create(environment=environment, requested_by=None, scheduled=True,
                                        config=environment.collection_config())
                count += 1
            # Missed periods coalesce into one job; failures retry at the next interval.
            environment.next_sync_at = now + timedelta(minutes=environment.sync_interval_minutes)
            environment.save(update_fields=["next_sync_at"])
    return count


def claim_job():
    with transaction.atomic():
        from django.db import connection
        jobs = AuditJob.objects.filter(status="queued").order_by("created_at")
        if connection.features.has_select_for_update_skip_locked:
            jobs = jobs.select_for_update(skip_locked=True)
        else:
            jobs = jobs.select_for_update()
        job = jobs.first()
        if job:
            job.status = "running"
            job.started_at = timezone.now()
            job.progress_stage = "Starting collection"
            job.save(update_fields=["status", "started_at", "progress_stage"])
        return job


def update_progress(job_id, completed, stage):
    """Never move backwards or update a finished/expired job; 100% requires a saved snapshot."""
    completed = max(0, min(6, completed))
    AuditJob.objects.filter(pk=job_id, status="running", progress_completed__lte=completed).update(
        progress_completed=completed, progress_stage=stage[:150])


def fail_job(job_id, message):
    AuditJob.objects.filter(pk=job_id, status="running").update(
        status="failed", finished_at=timezone.now(), error=message[:1500])


def expire_jobs():
    # A crashed worker must not leave an environment permanently locked.
    cutoff = timezone.now() - timedelta(seconds=settings.AUDIT_TIMEOUT + 300)
    return AuditJob.objects.filter(status="running", started_at__lt=cutoff).update(
        status="failed", finished_at=timezone.now(),
        error="Worker stopped or the audit exceeded its time limit. You can start a new audit.")


def prepare_snapshot(environment, report, imported=False):
    required = {"objects", "generated_at", "groups_scanned", "custom_services_scanned",
                "system_groups_excluded", "indexed_objects_scanned", "scope", "usage_definition", "limitations"}
    if not isinstance(report, dict) or required - report.keys() or not isinstance(report["objects"], list):
        raise ValidationError("Use an NSX Security Analyzer JSON report with inventory and audit metadata.")
    if not isinstance(report.get("manager"), str) or manager_origin(report["manager"]) != environment.manager:
        raise ValidationError("The report manager must match this environment's NSX Manager.")
    try:
        stamp = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
        if timezone.is_naive(stamp):
            raise ValueError()
        if stamp > timezone.now() + timedelta(minutes=5):
            raise ValueError()
        report = dict(report)
        report.pop("naming", None)
        if imported:
            report["rendered_from_saved_report"] = True
        audit = engine()
        audit.render_html_report(report)  # Validate renderable data before publishing.
        review = bool(audit.needs_review(report))
        dfw = report.get("dfw", {})
        summary = {"groups": report["groups_scanned"], "services": report["custom_services_scanned"],
                   "policies": len(dfw.get("policies", [])), "rules": len(dfw.get("rules", [])),
                   "tags": len(report.get("tags", {}).get("objects", []))}
        from .usability import coverage_keys
        if not imported and not report.get('testing'):
            keys = coverage_keys(report)
            previous = environment.snapshots.filter(testing=False, imported=False).order_by('-generated_at', '-created_at').values_list('summary', flat=True).first()
            # Legacy snapshots have no comparable keys; establish a baseline first.
            summary['coverage_issue_keys'] = keys
            summary['new_coverage_issues'] = len(set(keys) - set((previous or {}).get('coverage_issue_keys', []))) if previous is None or 'coverage_issue_keys' in previous else 0
        # Reject values which cannot be stored portably in PostgreSQL JSON.
        json.dumps(report, allow_nan=False)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise ValidationError("The saved report contains invalid or unsupported audit data.") from exc
    return Snapshot(environment=environment, report=report, summary=summary,
                    generated_at=stamp, testing=bool(report.get("testing")), needs_review=review, imported=imported)


def execute_job(job_id):
    job = AuditJob.objects.select_related("environment").get(pk=job_id)
    if job.status != "running":
        return
    secrets = []

    def redact(value):
        if isinstance(value, str):
            for secret in sorted(set(secrets), key=len, reverse=True):
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        return value

    try:
        config = job.config
        if config.get("password_ciphertext"):
            username = config["username"]
            password = decrypt_password(config["password_ciphertext"])
        else:
            # Compatibility for existing environments and jobs created before saved passwords.
            username = os.environ.get(config["username_env"]) if config.get("username_env") else config["username"]
            password = os.environ.get(config.get("password_env", ""))
        if password:
            secrets.append(password)
        if not username or not password:
            raise ValidationError("Credentials are missing or empty. Enter a username and password in Edit environment.")
        secrets.append(base64.b64encode((username + ":" + password).encode()).decode())
        audit = engine()
        audit.configure_logging()
        for handler in audit.LOG.handlers:
            handler.setFormatter(audit.DiagnosticFormatter(secrets, debug=False))
        client = audit.NSXClient(config["manager"], username, password, timeout=config["timeout"],
                                 ca_bundle=None if config.get("ca_certificate") else config.get("ca_bundle") or None,
                                 ca_data=config.get("ca_certificate") or None, insecure=config["insecure"],
                                 retries=config["retries"])
        from urllib.parse import urlsplit
        previous = job.environment.snapshots.filter(testing=False).only("report").first()
        client.statistics_backoff = audit.statistics_backoff(previous.report if previous else None,
                                                             urlsplit(client.base_url).netloc)
        concurrency = None if job.testing else adapt_requests(client)
        report = audit.audit(client, workers=1 if job.testing else concurrency.maximum, testing=job.testing,
                             progress=lambda completed, stage: update_progress(job.pk, completed, stage))
        if concurrency:
            report.setdefault("performance", {})["concurrency"] = concurrency.summary()
            report["performance"]["workers"] = concurrency.peak
        update_progress(job.pk, 5, "Preparing report and hit history")
        from urllib.parse import urlsplit
        report["manager"] = urlsplit(client.base_url).netloc
        audit.retain_hit_history(report, previous.report if previous else None)
        snapshot = prepare_snapshot(job.environment, redact(report))
        update_progress(job.pk, 6, "Saving snapshot")
        with transaction.atomic():
            current = AuditJob.objects.select_for_update().get(pk=job.pk)
            if current.status != "running":
                return  # Expired jobs cannot publish a late result.
            snapshot.job = current
            snapshot.save()
            current.status = "succeeded"
            current.finished_at = timezone.now()
            current.progress_completed = 7
            current.progress_stage = "Completed"
            current.save(update_fields=["status", "finished_at", "progress_completed", "progress_stage"])
    except Exception as exc:
        detail = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        fail_job(job.pk, redact(detail) or "Audit failed. Check the worker configuration.")
