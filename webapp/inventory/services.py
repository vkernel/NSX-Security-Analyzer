"""Database-backed collection jobs and read-only NSX worker integration."""
import base64
import json
import time
from datetime import datetime, timedelta
from functools import lru_cache
from contextlib import contextmanager

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AuditJob, Environment, Snapshot, manager_origin
from .credentials import decrypt_password
from .diagnostics import LOG, phase, log_failure
from .concurrency import adapt_requests


@lru_cache(maxsize=1)
def engine():
    from . import collector
    return collector


def enqueue(environment, user, testing=False, debug_until=None):
    with transaction.atomic():
        environment = Environment.objects.select_for_update().filter(pk=environment.pk).first()
        if environment is None:
            raise ValidationError("This environment has been deleted.")
        if not environment.enabled:
            raise ValidationError("This environment is paused. Enable it before collecting.")
        if environment.jobs.filter(status__in=["queued", "running"]).exists():
            raise ValidationError("An audit is already queued or running for this environment.")
        return AuditJob.objects.create(environment=environment, requested_by=user,
                                       testing=testing, debug_until=debug_until, config=environment.collection_config())


def schedule_due():
    """Persist schedules and serialize with manual collection and environment edits."""
    now = timezone.now()
    count = 0
    from django.db.models import Q
    candidates = Environment.objects.filter(enabled=True, sync_interval_minutes__gt=0).filter(
        Q(next_sync_at__lte=now) | Q(next_sync_at__isnull=True)).values_list("pk", flat=True)
    for pk in list(candidates):
        with transaction.atomic():
            environment = Environment.objects.select_for_update().filter(pk=pk).first()
            if environment is None:
                continue  # Deleted after the scheduler read its candidate list.
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
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(781249310)")
        jobs = AuditJob.objects.filter(status="queued").order_by("created_at")
        if connection.features.has_select_for_update_skip_locked:
            jobs = jobs.select_for_update(skip_locked=True)
        else:
            jobs = jobs.select_for_update()
        from urllib.parse import urlsplit
        def manager_key(config):
            origin = config.get("manager", "")
            parsed = urlsplit(origin if "://" in origin else "https://" + origin)
            return (parsed.hostname or "").lower(), parsed.port or 443
        busy = {manager_key(config) for config in AuditJob.objects.filter(status="running").values_list("config", flat=True)}
        job = next((candidate for candidate in jobs.iterator() if manager_key(candidate.config) not in busy), None)
        if job:
            job.status = "running"
            job.started_at = timezone.now()
            job.progress_stage = "Starting collection"
            job.save(update_fields=["status", "started_at", "progress_stage"])
        return job


def update_progress(job_id, completed, stage):
    """Never move backwards or update a finished/expired job; 100% requires a saved snapshot."""
    LOG.info("job=%s progress=%s/7 stage=%s", job_id, completed, stage)
    completed = max(0, min(6, completed))
    AuditJob.objects.filter(pk=job_id, status="running", progress_completed__lte=completed).update(
        progress_completed=completed, progress_stage=stage[:150])


@transaction.atomic
def fail_job(job_id, message, diagnostics=None):
    from .audit_events import record
    from .observability import safe_data
    extra = {'diagnostics': safe_data(diagnostics)} if diagnostics is not None else {}
    changed = AuditJob.objects.filter(pk=job_id, status="running").update(
        status="failed", finished_at=timezone.now(), error=message[:1500], **extra)
    if changed:
        from .history_cache import invalidate
        invalidate(AuditJob.objects.values_list('environment_id', flat=True).get(pk=job_id))
        record('collection.failed', 'AuditJob', job_id, outcome='failed',
               details={'code': (diagnostics or {}).get('error', {}).get('code', 'COLLECTION_FAILED')}, best_effort=True)


class CollectionStopped(Exception):
    pass


@contextmanager
def cancellation_database_timeout():
    """Bound lock/SQL waits without changing the enclosing snapshot transaction's settings."""
    from django.db import connection
    with transaction.atomic():
        if connection.vendor != 'postgresql':
            yield
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('statement_timeout'), current_setting('lock_timeout')")
            previous = cursor.fetchone()
            cursor.execute("SET LOCAL statement_timeout = '5s'")
            cursor.execute("SET LOCAL lock_timeout = '2s'")
        yield
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)", previous)


def stop_requested(job_id):
    from .models import CollectionStopRequest
    with cancellation_database_timeout():
        return CollectionStopRequest.objects.filter(job_id=job_id).exists()


@transaction.atomic
def finish_stopped_job(job_id):
    from .audit_events import record
    changed = AuditJob.objects.filter(pk=job_id, status__in=["queued", "running"]).update(
        status="cancelled", finished_at=timezone.now(), progress_stage="Stopped by operator", error="")
    if changed:
        record('collection.stopped', 'AuditJob', job_id)
        LOG.info("job=%s collection stopped by operator", job_id)
    return changed


@cancellation_database_timeout()
def request_collection_stop(job_id, actor):
    from .models import CollectionStopRequest
    from .audit_events import record
    from django.db import connection
    if not AuditJob.objects.filter(pk=job_id, status__in=["queued", "running"]).exists():
        return False
    _, created = CollectionStopRequest.objects.get_or_create(job_id=job_id,
        defaults={"actor_id_text": str(actor.pk)})
    if created:
        record('collection.stop_requested', 'AuditJob', job_id, actor=actor.pk)
    # Cancel an unclaimed job immediately. Never wait for a running collector's
    # snapshot transaction; its supervisor will kill the process and roll it back.
    queued = AuditJob.objects.filter(pk=job_id, status="queued")
    queued = queued.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else queued.select_for_update()
    if queued.only('pk').first():
        finish_stopped_job(job_id)
    return True


def expire_jobs():
    # A crashed worker must not leave an environment permanently locked.
    cutoff = timezone.now() - timedelta(seconds=settings.AUDIT_TIMEOUT + 300)
    ids = list(AuditJob.objects.filter(status="running", started_at__lt=cutoff).values_list('pk', flat=True)[:100])
    for job_id in ids:
        LOG.warning("job=%s stale collection expired timeout_seconds=%s grace_seconds=300", job_id, settings.AUDIT_TIMEOUT)
        fail_job(job_id, "Worker stopped or the audit exceeded its time limit. You can start a new audit.",
                 {'error': {'code': 'WORKER_STALE', 'message': 'No terminal job state recorded before timeout plus grace period.'}})
    return len(ids)


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
        rendered = None  # Index builder prepares and inserts rows incrementally.
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
    snapshot = Snapshot(environment=environment, report=report, summary=summary,
                    generated_at=stamp, testing=bool(report.get("testing")), needs_review=review, imported=imported)
    snapshot._rendered = rendered
    return snapshot


def execute_job(job_id):
    job = AuditJob.objects.select_related("environment").get(pk=job_id)
    if job.status != "running":
        return
    from . import observability, diagnostics as diagnostic_log
    from .audit_events import record
    diagnostic_log.TIMELINE.clear()
    observability.PROCESS_CONTEXT.update(job_id=str(job.pk), environment_id=job.environment_id, service='collector')
    started = time.monotonic()
    LOG.info("job=%s environment_id=%s collection started", job.pk, job.environment_id)
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
        username = config.get("username")
        password = decrypt_password(config["password_ciphertext"]) if config.get("password_ciphertext") else None
        if password:
            secrets.append(password)
        if not username or not password:
            raise ValidationError("Credentials are missing or empty. Enter a username and password in Edit environment.")
        secrets.append(base64.b64encode((username + ":" + password).encode()).decode())
        audit = engine()
        observability.PROCESS_SECRETS = tuple(secrets)
        audit.configure_logging(debug=bool(job.debug_until and job.debug_until > timezone.now()))
        for handler in audit.LOG.handlers:
            handler.setFormatter(observability.ConsoleFormatter())
            handler.addFilter(observability.ScopedDebug(job.debug_until))
        client = audit.NSXClient(config["manager"], username, password, timeout=config["timeout"],
                                 ca_bundle=None if config.get("ca_certificate") else config.get("ca_bundle") or None,
                                 ca_data=config.get("ca_certificate") or None, insecure=config["insecure"],
                                 retries=config["retries"])
        from urllib.parse import urlsplit
        with phase(job.pk, "load_previous_snapshot"):
            previous = job.environment.snapshots.filter(testing=False).only("report").first()
        client.statistics_backoff = audit.statistics_backoff(previous.report if previous else None,
                                                             urlsplit(client.base_url).netloc)
        client.membership_hints = {}
        for row in (previous.report if previous else {}).get("objects", []):
            if row.get("kind") == "group" and row.get("membership") == "nonempty":
                for note in row.get("notes", []):
                    prefix = "Resolved members found: "
                    if note.startswith(prefix) and note[len(prefix):] in audit.MEMBERSHIP_ENDPOINTS:
                        client.membership_hints[row["path"]] = note[len(prefix):]
        client.request_deadline = time.monotonic() + max(0, settings.AUDIT_TIMEOUT - (timezone.now()-job.started_at).total_seconds() - 10) if job.started_at else time.monotonic() + settings.AUDIT_TIMEOUT - 10
        concurrency = None if job.testing else adapt_requests(client)
        try:
            with phase(job.pk, "retrieve_nsx_inventory"):
                report = audit.audit(client, workers=1 if job.testing else concurrency.maximum, testing=job.testing,
                                 progress=lambda completed, stage: update_progress(job.pk, completed, stage))
        finally:
            if concurrency:
                LOG.info("NSX request pacing summary: %s", client.request_pacer.summary())
            client.close()
        if concurrency:
            report.setdefault("performance", {})["concurrency"] = concurrency.summary()
            report["performance"]["workers"] = concurrency.peak
            report["performance"]["request_pacing"] = client.request_pacer.summary()
        update_progress(job.pk, 5, "Preparing report and hit history")
        from urllib.parse import urlsplit
        report["manager"] = urlsplit(client.base_url).netloc
        audit.retain_hit_history(report, previous.report if previous else None)
        with phase(job.pk, "prepare_snapshot"):
            snapshot = prepare_snapshot(job.environment, redact(report))
        update_progress(job.pk, 6, "Saving snapshot")
        if stop_requested(job.pk):
            raise CollectionStopped()
        with phase(job.pk, "snapshot_transaction"), transaction.atomic():
            with phase(job.pk, "lock_collection_job"):
                current = AuditJob.objects.select_for_update().get(pk=job.pk)
            if current.status != "running":
                LOG.warning("job=%s snapshot discarded status=%s", job.pk, current.status)
                return  # Expired jobs cannot publish a late result.
            snapshot.job = current
            with phase(job.pk, "write_snapshot"):
                snapshot.save()
            from .snapshot_index import build
            with phase(job.pk, "index_snapshot"):
                build(snapshot, snapshot._rendered)
            from .findings import synchronize
            with phase(job.pk, "synchronize_findings"):
                synchronize(job.environment_id)
            if stop_requested(job.pk):
                raise CollectionStopped()
            current.status = "succeeded"
            current.finished_at = timezone.now()
            current.progress_completed = 7
            current.progress_stage = "Completed"
            with phase(job.pk, "mark_collection_completed"):
                current.save(update_fields=["status", "finished_at", "progress_completed", "progress_stage"])
            LOG.info("job=%s transaction ready_to_commit", job.pk)
            commit_started = time.monotonic()
        LOG.info("job=%s commit_seconds=%.3f", job.pk, time.monotonic()-commit_started)
        LOG.info("job=%s collection committed elapsed_seconds=%.1f", job.pk, time.monotonic() - started)
        record('collection.completed', 'AuditJob', job.pk,
               details={'snapshot_id': str(snapshot.pk), 'elapsed_seconds': round(time.monotonic()-started, 3)}, best_effort=True)
        # Diagnostics are supplemental: a telemetry write failure cannot undo success.
        try:
            AuditJob.objects.filter(pk=job.pk, status='succeeded').update(diagnostics={'timeline': diagnostic_log.TIMELINE})
        except Exception as exc:
            log_failure(job.pk, exc)
    except CollectionStopped:
        finish_stopped_job(job.pk)
    except Exception as exc:
        failure = log_failure(job.pk, exc)
        detail = failure['chain'][0]['message'] if failure['chain'] else 'Collection failed.'
        fail_job(job.pk, redact(detail) or "Audit failed. Check the worker configuration.",
                 {'timeline': diagnostic_log.TIMELINE, 'error': failure})
    finally:
        observability.PROCESS_CONTEXT.clear()
        observability.PROCESS_SECRETS = ()
