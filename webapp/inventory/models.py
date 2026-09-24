import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

env_name = RegexValidator(r"^[A-Za-z_][A-Za-z0-9_]*$", "Use an environment variable name.")


def manager_origin(value):
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or any(c.isspace() for c in value)):
            raise ValueError()
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        return "https://" + host + (":" + str(parsed.port) if parsed.port and parsed.port != 443 else "")
    except ValueError as exc:
        raise ValidationError("Enter an HTTPS manager hostname or origin URL.") from exc


class Environment(models.Model):
    slug = models.SlugField(unique=True, max_length=64)
    name = models.CharField(max_length=150)
    manager = models.CharField(max_length=255, unique=True)
    username = models.CharField(max_length=150, default="admin", blank=True)
    username_env = models.CharField(max_length=150, blank=True, validators=[env_name])
    password_env = models.CharField(max_length=150, blank=True, validators=[env_name])
    password_ciphertext = models.TextField(blank=True, editable=False)
    ca_bundle = models.CharField(max_length=500, blank=True)  # Legacy worker path.
    ca_certificate = models.TextField(blank=True, editable=False)
    ca_filename = models.CharField(max_length=255, blank=True, editable=False)
    insecure = models.BooleanField(default=False)
    workers = models.PositiveSmallIntegerField(default=4, validators=[MinValueValidator(1), MaxValueValidator(16)])
    timeout = models.PositiveIntegerField(default=30, validators=[MinValueValidator(1), MaxValueValidator(300)])
    retries = models.PositiveSmallIntegerField(default=2, validators=[MaxValueValidator(5)])
    enabled = models.BooleanField(default=True)
    sync_interval_minutes = models.PositiveIntegerField(default=60, choices=[
        (0, "Manual only"), (15, "Every 15 minutes"), (30, "Every 30 minutes"),
        (60, "Every hour"), (360, "Every 6 hours"), (720, "Every 12 hours"),
        (1440, "Daily"), (10080, "Weekly")])
    next_sync_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["name"]

    def clean(self):
        self.manager = manager_origin(self.manager)
        if not self.username_env and not self.username.strip():
            raise ValidationError("Provide a username or username environment variable.")

    def __str__(self):
        return self.name

    def collection_config(self):
        return {key: getattr(self, key) for key in (
            "manager", "username", "username_env", "password_env", "password_ciphertext", "ca_bundle", "ca_certificate", "insecure",
            "workers", "timeout", "retries")}


class AuditJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.ForeignKey(Environment, on_delete=models.PROTECT, related_name="jobs")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    testing = models.BooleanField(default=False)
    scheduled = models.BooleanField(default=False)
    config = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True, db_index=True)
    error = models.TextField(blank=True)
    progress_completed = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(7)])
    progress_stage = models.CharField(max_length=150, default="Waiting for an audit worker")

    @property
    def progress(self):
        completed = 7 if self.status == "succeeded" else min(self.progress_completed, 6)
        stage = ("Completed" if self.status == "succeeded" else
                 "Stopped · " + self.progress_stage if self.status == "failed" else self.progress_stage)
        return {"completed": completed, "total": 7, "percent": completed * 100 // 7, "stage": stage}

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["environment"],
            condition=models.Q(status__in=["queued", "running"]), name="one_active_audit_per_environment")]


class Snapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.ForeignKey(Environment, on_delete=models.PROTECT, related_name="snapshots")
    job = models.OneToOneField(AuditJob, null=True, blank=True, on_delete=models.PROTECT, related_name="snapshot")
    created_at = models.DateTimeField(auto_now_add=True)
    generated_at = models.DateTimeField()
    testing = models.BooleanField(default=False)
    needs_review = models.BooleanField(default=False)
    imported = models.BooleanField(default=False)
    summary = models.JSONField(default=dict)
    report = models.JSONField()
    # Retained for existing installations; never read or populated by the report viewer.
    html = models.TextField(blank=True, default="", editable=False)

    class Meta:
        ordering = ["-generated_at", "-created_at"]


class UserPreferences(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    page_size = models.PositiveSmallIntegerField(default=20, choices=[(n, str(n)) for n in (10, 20, 50, 100)])
    report_page_size = models.PositiveSmallIntegerField(default=25, choices=[(n, str(n)) for n in (25, 50, 100)])
    density = models.CharField(max_length=15, default='comfortable', choices=[('comfortable', 'Comfortable'), ('compact', 'Compact')])
    history_days = models.PositiveSmallIntegerField(default=30, choices=[(n, f'{n} days') for n in (7, 30, 90)])
    refresh_seconds = models.PositiveSmallIntegerField(default=5, choices=[(n, f'{n} seconds') for n in (5, 10, 30, 60)])
    timezone = models.CharField(max_length=64, default='UTC')
    date_format = models.CharField(max_length=12, default='readable', choices=[('readable', '23 Sep 2026'), ('iso', '2026-09-23'), ('day-first', '23/09/2026'), ('month-first', '09/23/2026')])
    theme = models.CharField(max_length=8, default='system', choices=[('system', 'Follow system'), ('light', 'Light'), ('dark', 'Dark')])
    text_size = models.CharField(max_length=8, default='normal', choices=[('normal', 'Normal'), ('large', 'Large')])
    high_contrast = models.BooleanField(default=False)
    reduced_motion = models.BooleanField(default=False)
    remember_tables = models.BooleanField(default=True)
    remember_menus = models.BooleanField(default=True)
    landing_page = models.CharField(max_length=16, default='overview', choices=[('overview', 'Overview'), ('environment', 'Preferred environment'), ('activity', 'Firewall activity')])
    preferred_environment = models.ForeignKey(Environment, null=True, blank=True, on_delete=models.SET_NULL)
    notifications_seen_at = models.DateTimeField(null=True, editable=False)
    interface_state = models.JSONField(default=dict, editable=False)


class RetentionPolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    snapshot_days = models.PositiveIntegerField(default=180, choices=[(0, 'Keep forever'), (91, '91 days'), (180, '180 days'), (365, '1 year'), (730, '2 years')])
    testing_days = models.PositiveIntegerField(default=30, choices=[(0, 'Keep forever'), (7, '7 days'), (30, '30 days'), (91, '91 days')])
    collection_days = models.PositiveIntegerField(default=180, choices=[(0, 'Keep forever'), (91, '91 days'), (180, '180 days'), (365, '1 year'), (730, '2 years')])
    last_run = models.DateTimeField(null=True, editable=False)
    deleted_snapshots = models.PositiveIntegerField(default=0, editable=False)
    deleted_collections = models.PositiveIntegerField(default=0, editable=False)


class WorkspacePolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    stale_hours = models.PositiveIntegerField(default=24, validators=[MinValueValidator(1), MaxValueValidator(8760)])
    notify_failed = models.BooleanField(default=True)
    notify_completed = models.BooleanField(default=True)
    notify_coverage = models.BooleanField(default=True)
