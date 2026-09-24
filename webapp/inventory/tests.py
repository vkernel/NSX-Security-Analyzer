import copy
import io
import json
import os
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, TransactionTestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AuditJob, Environment, Snapshot, manager_origin
from .credentials import encrypt_password
from .services import claim_job, engine, enqueue, execute_job, expire_jobs, prepare_snapshot, update_progress, fail_job, schedule_due


def sample_report():
    return {
        "manager": "east.example", "generated_at": timezone.now().isoformat(),
        "objects": [{"name": "prod web", "path": "/infra/domains/default/groups/prod-web", "kind": "group",
                     "usage": "referenced", "membership": "nonempty", "referenced_by": [], "notes": []}],
        "groups_scanned": 1, "custom_services_scanned": 0, "system_groups_excluded": 0,
        "indexed_objects_scanned": 1, "scope": "Local Manager /infra", "testing": False,
        "usage_definition": "No configuration reference found", "limitations": "Visible inventory only",
        "dfw": {"policies": [], "rules": [], "errors": []},
    }


@override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class WorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operator = get_user_model().objects.create_user("operator", password="local-tests-only", is_staff=True)
        cls.viewer = get_user_model().objects.create_user("viewer", password="local-tests-only")
        cls.environment = Environment.objects.create(slug="east", name="East Datacenter",
            manager="https://east.example", username="reader", password_ciphertext=encrypt_password("private-secret"))

    def setUp(self):
        self.client.force_login(self.operator)

    def make_snapshot(self):
        snapshot = prepare_snapshot(self.environment, sample_report(), imported=True)
        snapshot.save()
        return snapshot

    def test_anonymous_cannot_access_inventory_or_reports(self):
        snapshot = self.make_snapshot()
        self.client.logout()
        for url in [reverse("dashboard"), reverse("api-jobs"), reverse("environment", args=[self.environment.pk]),
                    reverse("snapshot", args=[snapshot.pk]), reverse("report-content", args=[snapshot.pk])]:
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_viewers_can_read_but_cannot_mutate(self):
        self.client.force_login(self.viewer)
        snapshot = self.make_snapshot()
        self.assertEqual(self.client.get(reverse("snapshot", args=[snapshot.pk])).status_code, 200)
        for url in [reverse("collect", args=[self.environment.pk]), reverse("environment-new"),
                    reverse("environment-edit", args=[self.environment.pk]),
                    reverse("testing-data")]:
            self.assertEqual(self.client.post(url, {}).status_code, 403, url)
        self.assertEqual(AuditJob.objects.count(), 0)

    def test_collect_requires_post_and_csrf(self):
        url = reverse("collect", args=[self.environment.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        self.assertEqual(client.post(url).status_code, 403)

    def test_enqueue_and_duplicate_prevention(self):
        url = reverse("collect", args=[self.environment.pk])
        self.assertEqual(self.client.post(url, {"testing": "1"}).status_code, 302)
        job = AuditJob.objects.get()
        self.assertFalse(job.testing)
        self.assertTrue(job.config["password_ciphertext"])
        self.client.post(url)
        self.assertEqual(AuditJob.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AuditJob.objects.create(environment=self.environment)

    def test_disabled_environment_cannot_queue(self):
        self.environment.enabled = False
        self.environment.save()
        with self.assertRaises(ValidationError):
            enqueue(self.environment, self.operator)

    def test_edit_locks_active_and_historical_manager_origin(self):
        form_data = dict(self.environment.collection_config(), name="Renamed", slug="east", enabled="on", sync_interval_minutes=60, password="form-test-secret")
        job = enqueue(self.environment, self.operator)
        url = reverse("environment-edit", args=[self.environment.pk])
        response = self.client.post(url, form_data)
        self.assertContains(response, "Wait for the active audit")
        job.status = "failed"
        job.save()
        self.make_snapshot()
        form_data["manager"] = "other.example"
        response = self.client.post(url, form_data)
        self.assertContains(response, "Create a new environment")
        self.environment.refresh_from_db()
        self.assertEqual(self.environment.manager, "https://east.example")

    def test_manager_validation(self):
        self.assertEqual(manager_origin("HTTPS://EAST.EXAMPLE:443/"), "https://east.example")
        self.assertEqual(manager_origin("https://[::1]:8443"), "https://[::1]:8443")
        for value in ["http://east.example", "https://user:pass@east.example", "https://east.example/path",
                      "https://east.example:bad", "https://east.example#fragment", "east example"]:
            with self.assertRaises(ValidationError, msg=value):
                manager_origin(value)

    def test_database_snapshot_renders_integrated_report_and_boolean_search(self):
        report = sample_report()
        report["objects"][0]["name"] = "<script>alert(1)</script>"
        snapshot = prepare_snapshot(self.environment, report, imported=True)
        snapshot.save()
        self.assertTrue(snapshot.imported)
        self.assertEqual(snapshot.html, "")
        Snapshot.objects.filter(pk=snapshot.pk).update(html="<script>legacyHtmlMustNeverRun()</script>")
        self.assertEqual(snapshot.report["generated_at"], report["generated_at"])
        # Each page is rendered from JSON loaded from the database, without NSX or saved HTML.
        with patch.object(engine(), "NSXClient", side_effect=AssertionError("Report contacted NSX")):
            response = self.client.get(reverse("snapshot", args=[snapshot.pk]))
        self.assertContains(response, "tableSearchMatcher")
        self.assertContains(response, "prod AND web OR staging")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, r"\u003cscript>alert(1)\u003c/script>")
        self.assertNotContains(response, "legacyHtmlMustNeverRun")
        self.assertNotContains(response, "<iframe")
        self.assertNotContains(response, "Download HTML")
        self.assertNotContains(response, "/download/")
        self.assertEqual(response.content.count(b'<main '), 1)
        self.assertEqual(response.content.count(b'<aside '), 1)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertRedirects(self.client.get(reverse("report-content", args=[snapshot.pk])),
                             reverse("snapshot", args=[snapshot.pk]))
        self.assertEqual(self.client.get(f"/snapshots/{snapshot.pk}/download/json/").status_code, 404)
        self.assertEqual(self.client.get(f"/snapshots/{snapshot.pk}/download/html/").status_code, 404)


    def test_invalid_imports_do_not_save(self):
        for report in [{}, {**sample_report(), "manager": "other.example"},
                       {**sample_report(), "generated_at": "not-a-date"},
                       {**sample_report(), "objects": ["invalid"]}]:
            with self.assertRaises(ValidationError):
                prepare_snapshot(self.environment, report, imported=True)
        self.assertEqual(Snapshot.objects.count(), 0)

    def test_dashboard_history_forms_render_and_escape(self):
        self.make_snapshot()
        for url in [reverse("dashboard"), reverse("environment", args=[self.environment.pk]),
                    reverse("environment-new"), reverse("testing-data")]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        job = enqueue(self.environment, self.operator)
        job.status, job.error = "failed", "<script>bad()</script>"
        job.save()
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "&lt;script&gt;bad()&lt;/script&gt;")
        self.assertNotIn("config", self.client.get(reverse("api-jobs")).json()["jobs"][0])

    def test_progress_is_monotonic_and_stops_on_failure(self):
        job = enqueue(self.environment, self.operator)
        update_progress(job.pk, 3, "Must not update queued jobs")
        job.refresh_from_db()
        self.assertEqual(job.progress["percent"], 0)
        claim_job()
        update_progress(job.pk, 3, "Checking membership")
        update_progress(job.pk, 1, "Old update")
        job.refresh_from_db()
        self.assertEqual(job.progress["completed"], 3)
        self.assertEqual(job.progress["stage"], "Checking membership")
        fail_job(job.pk, "Connection lost")
        update_progress(job.pk, 7, "Late update")
        job.refresh_from_db()
        self.assertEqual(job.progress["percent"], 42)
        self.assertEqual(job.progress["stage"], "Stopped · Checking membership")

    def test_active_progress_is_available_in_api_and_pages(self):
        job = enqueue(self.environment, self.operator)
        claim_job()
        update_progress(job.pk, 4, "Checking tags")
        response = self.client.get(reverse("api-jobs"), {"id": str(job.pk)})
        self.assertEqual(response.json()["jobs"][0]["progress"], {
            "completed": 4, "total": 7, "percent": 57, "stage": "Checking tags"})
        for url in [reverse("dashboard"), reverse("environment", args=[self.environment.pk])]:
            response = self.client.get(url)
            self.assertContains(response, '<progress value="4" max="7"')
            self.assertContains(response, "Checking tags")

    def test_successful_worker_publishes_snapshot_with_history(self):
        old = self.make_snapshot()
        job = enqueue(self.environment, self.operator)
        self.assertEqual(claim_job().pk, job.pk)
        self.assertIsNone(claim_job())
        audit = engine()
        with patch.dict(os.environ, {"NSX_USERNAME_EAST": "reader", "NSX_PASSWORD_EAST": "private-secret"}), \
                patch.object(audit, "NSXClient") as client, patch.object(audit, "audit", return_value=sample_report()), \
                patch.object(audit, "retain_hit_history") as history:
            client.return_value.base_url = "https://east.example/policy/api/v1"
            execute_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.progress["percent"], 100)
        self.assertEqual(job.snapshot.report["performance"]["concurrency"]["mode"], "automatic")
        self.assertEqual(Snapshot.objects.count(), 2)
        self.assertEqual(history.call_args.args[1], old.report)
        self.assertEqual(job.snapshot.environment, self.environment)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_worker_redacts_errors_and_preserves_previous_snapshot(self):
        old = self.make_snapshot()
        job = enqueue(self.environment, self.operator)
        claim_job()
        audit = engine()
        with patch.dict(os.environ, {"NSX_USERNAME_EAST": "reader", "NSX_PASSWORD_EAST": "private-secret"}), \
                patch.object(audit, "NSXClient", side_effect=audit.AuditError("Failure private-secret")):
            execute_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error, "Failure [REDACTED]")
        self.assertEqual(Snapshot.objects.get().pk, old.pk)

    def test_worker_redacts_notes_before_saving_report(self):
        job = enqueue(self.environment, self.operator)
        claim_job()
        report = sample_report()
        report["objects"][0]["notes"] = ["Failure private-secret"]
        audit = engine()
        with patch.dict(os.environ, {"NSX_USERNAME_EAST": "reader", "NSX_PASSWORD_EAST": "private-secret"}), \
                patch.object(audit, "NSXClient") as client, patch.object(audit, "audit", return_value=report):
            client.return_value.base_url = "https://east.example/policy/api/v1"
            execute_job(job.pk)
        snapshot = Snapshot.objects.get()
        self.assertNotContains(self.client.get(reverse("snapshot", args=[snapshot.pk])), "private-secret")
        self.assertNotIn("private-secret", json.dumps(snapshot.report))

    def test_missing_credentials_fail_without_prompt_or_network(self):
        self.environment.password_ciphertext = ""
        self.environment.save()
        job = enqueue(self.environment, self.operator)
        claim_job()
        with patch.dict(os.environ, {"NSX_USERNAME_EAST":"reader", "NSX_PASSWORD_EAST":"ignored-legacy-password"}), patch.object(engine(), "NSXClient") as client:
            execute_job(job.pk)
        client.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("missing or empty", job.error)

    @override_settings(AUDIT_TIMEOUT=60)
    def test_expired_worker_releases_environment(self):
        job = enqueue(self.environment, self.operator)
        claim_job()
        AuditJob.objects.filter(pk=job.pk).update(started_at=timezone.now() - timedelta(minutes=10))
        self.assertEqual(expire_jobs(), 1)
        execute_job(job.pk)
        self.assertEqual(Snapshot.objects.count(), 0)
        self.assertIsNotNone(enqueue(self.environment, self.operator))

    def test_administration_permissions_and_branding(self):
        response = self.client.get(reverse("administration"))
        self.assertContains(response, "Automatic sync")
        self.assertContains(response, reverse("environment-new"))
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("administration")).status_code, 403)
        self.assertEqual(self.client.get("/admin/").status_code, 403)
        self.operator.is_superuser = True
        self.operator.save()
        self.client.force_login(self.operator)
        for url in [reverse("admin:index"), reverse("admin:auth_user_changelist"),
                    reverse("admin:auth_user_add"), reverse("admin:auth_user_change", args=[self.viewer.pk])]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "workspace-sidebar")
            self.assertNotContains(response, "Django administration")
            self.assertNotContains(response, "Django site admin")

    def test_branded_user_administration_can_create_an_account(self):
        self.operator.is_superuser = True
        self.operator.save()
        response = self.client.post(reverse("admin:auth_user_add"), {
            "username": "new-viewer", "usable_password": "true",
            "password1": "Test-only-access-729!", "password2": "Test-only-access-729!", "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        account = get_user_model().objects.get(username="new-viewer")
        self.assertTrue(account.check_password("Test-only-access-729!"))
        self.assertFalse(account.is_staff)

    def test_schedule_initializes_then_queues_once_and_retries_next_interval(self):
        self.assertEqual(schedule_due(), 0)
        self.environment.refresh_from_db()
        self.assertGreater(self.environment.next_sync_at, timezone.now())
        Environment.objects.filter(pk=self.environment.pk).update(next_sync_at=timezone.now() - timedelta(days=2))
        self.assertEqual(schedule_due(), 1)
        self.assertEqual(schedule_due(), 0)
        job = AuditJob.objects.get()
        self.assertTrue(job.scheduled)
        self.assertIsNone(job.requested_by)
        self.assertFalse(job.testing)
        job.status = "failed"
        job.save()
        self.assertEqual(schedule_due(), 0)
        Environment.objects.filter(pk=self.environment.pk).update(next_sync_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(schedule_due(), 1)

    def test_schedule_skips_paused_manual_and_active_environments(self):
        Environment.objects.filter(pk=self.environment.pk).update(next_sync_at=timezone.now() - timedelta(seconds=1), enabled=False)
        self.assertEqual(schedule_due(), 0)
        Environment.objects.filter(pk=self.environment.pk).update(enabled=True, sync_interval_minutes=0)
        self.assertEqual(schedule_due(), 0)
        Environment.objects.filter(pk=self.environment.pk).update(sync_interval_minutes=15)
        job = enqueue(self.environment, self.operator)
        self.assertEqual(schedule_due(), 0)
        self.assertEqual(AuditJob.objects.count(), 1)
        job.status = "succeeded"
        job.save()
        self.assertEqual(schedule_due(), 1)

    def test_schedule_form_changes_next_run_and_manual_clears_it(self):
        from .forms import EnvironmentForm
        data = self.credential_form_data()
        data["sync_interval_minutes"] = 15
        form = EnvironmentForm(data, instance=self.environment)
        self.assertTrue(form.is_valid(), form.errors)
        environment = form.save()
        self.assertAlmostEqual((environment.next_sync_at-timezone.now()).total_seconds(), 900, delta=5)
        data["sync_interval_minutes"] = 0
        form = EnvironmentForm(data, instance=environment)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.save().next_sync_at)
        data["sync_interval_minutes"] = -1
        self.assertFalse(EnvironmentForm(data, instance=environment).is_valid())

    def test_health(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("health")).json(), {"status": "ok"})


    def credential_form_data(self, password="manager-secret-test"):
        return dict(self.environment.collection_config(), name="East", slug="east", enabled="on",
                    username="nsx-reader", password=password, sync_interval_minutes=60)

    def test_environment_form_has_masked_password_and_no_variable_fields(self):
        response = self.client.get(reverse("environment-new"))
        self.assertContains(response, 'type="password"')
        self.assertNotContains(response, 'name="username_env"')
        self.assertNotContains(response, 'name="password_env"')
        self.assertNotContains(response, 'name="password_ciphertext"')
        self.assertNotContains(response, 'name="workers"')

    def test_password_is_encrypted_and_not_redisplayed(self):
        from .forms import EnvironmentForm
        from .credentials import decrypt_password
        form = EnvironmentForm(self.credential_form_data(), instance=self.environment)
        self.assertTrue(form.is_valid(), form.errors)
        environment = form.save()
        environment.refresh_from_db()
        self.assertNotIn("manager-secret-test", environment.password_ciphertext)
        self.assertEqual(decrypt_password(environment.password_ciphertext), "manager-secret-test")
        self.assertEqual(environment.username_env, "")
        self.assertEqual(environment.password_env, "")
        response = self.client.get(reverse("environment-edit", args=[environment.pk]))
        self.assertNotContains(response, "manager-secret-test")
        self.assertNotContains(response, environment.password_ciphertext)
        job = enqueue(environment, self.operator)
        self.assertNotIn("manager-secret-test", json.dumps(job.config))

    def test_blank_edit_preserves_password_and_replacement_changes_it(self):
        from .forms import EnvironmentForm
        from .credentials import decrypt_password
        first = EnvironmentForm(self.credential_form_data(), instance=self.environment)
        self.assertTrue(first.is_valid(), first.errors)
        environment = first.save()
        ciphertext = environment.password_ciphertext
        edit = EnvironmentForm(self.credential_form_data(password=""), instance=environment)
        self.assertTrue(edit.is_valid(), edit.errors)
        self.assertEqual(edit.save().password_ciphertext, ciphertext)
        replacement = EnvironmentForm(self.credential_form_data(password=" new password "), instance=environment)
        self.assertTrue(replacement.is_valid(), replacement.errors)
        self.assertEqual(decrypt_password(replacement.save().password_ciphertext), " new password ")

    def test_password_required_without_saved_credentials(self):
        self.environment.password_ciphertext = ""
        from .forms import EnvironmentForm
        data = self.credential_form_data(password="")
        for instance in [None, self.environment]:
            form = EnvironmentForm(data, instance=instance)
            self.assertFalse(form.is_valid())
            self.assertIn("password", form.errors)

    def test_invalid_form_does_not_echo_submitted_password(self):
        data = self.credential_form_data()
        data["manager"] = "http://invalid.example"
        response = self.client.post(reverse("environment-edit", args=[self.environment.pk]), data)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "manager-secret-test")

    def test_wrong_encryption_key_fails_without_exposing_secret(self):
        from .credentials import encrypt_password, decrypt_password
        encrypted = encrypt_password("manager-secret-test")
        with override_settings(SECRET_KEY="a-different-app-key"):
            with self.assertRaisesMessage(ValidationError, "Re-enter it"):
                decrypt_password(encrypted)

    def test_worker_uses_saved_credentials_and_redacts_them(self):
        from .forms import EnvironmentForm
        form = EnvironmentForm(self.credential_form_data(), instance=self.environment)
        self.assertTrue(form.is_valid(), form.errors)
        environment = form.save()
        job = enqueue(environment, self.operator)
        claim_job()
        audit = engine()
        report = sample_report()
        report["objects"][0]["notes"] = ["manager-secret-test"]
        with patch.dict(os.environ, {}, clear=True), patch.object(audit, "NSXClient") as client, \
                patch.object(audit, "audit", return_value=report):
            client.return_value.base_url = "https://east.example/policy/api/v1"
            execute_job(job.pk)
        self.assertEqual(client.call_args.args[:3], ("https://east.example", "nsx-reader", "manager-secret-test"))
        job.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        self.assertNotIn("manager-secret-test", json.dumps(job.snapshot.report))
        self.assertNotContains(self.client.get(reverse("snapshot", args=[job.snapshot.pk])), "manager-secret-test")


class WorkerTransactionTests(TransactionTestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_user("worker-test", password="local-tests-only", is_staff=True)
        self.environment = Environment.objects.create(slug="worker-east", name="Worker East",
            manager="https://east.example", username="reader", password_ciphertext=encrypt_password("private-secret"))

    def test_worker_timeout_marks_failed(self):
        from subprocess import TimeoutExpired
        job = enqueue(self.environment, self.operator)
        with patch("inventory.management.commands.audit_worker.subprocess.Popen") as process:
            child = process.return_value.__enter__.return_value
            child.wait.side_effect = [TimeoutExpired("collector", 1), 0]
            call_command("audit_worker", once=True, stdout=io.StringIO())
            child.kill.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("timed out", job.error)

    def test_parallel_schedulers_queue_only_one_job(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connection, connections
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL scheduler concurrency check")
        Environment.objects.filter(pk=self.environment.pk).update(next_sync_at=timezone.now()-timedelta(minutes=1))
        def tick():
            try:
                return schedule_due()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(lambda _: tick(), range(2))), 1)
        self.assertEqual(AuditJob.objects.count(), 1)

    def test_parallel_claims_do_not_duplicate_jobs(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connection, connections
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock concurrency check")
        second = Environment.objects.create(slug="worker-west", name="Worker West",
            manager="https://west.example", password_env="NSX_PASSWORD_WEST")
        expected = {enqueue(self.environment, self.operator).pk, enqueue(second, self.operator).pk}
        def claim():
            try:
                job = claim_job()
                return job.pk if job else None
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            actual = set(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(actual, expected)
        self.assertIsNone(claim_job())

    def test_parallel_enqueues_keep_one_active_job(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connection, connections
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock concurrency check")
        def submit():
            try:
                enqueue(self.environment, self.operator)
                return "queued"
            except ValidationError:
                return "duplicate"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertCountEqual(results, ["queued", "duplicate"])
        self.assertEqual(AuditJob.objects.count(), 1)


class DatabaseConfigurationTests(SimpleTestCase):
    def database_settings(self, **values):
        import runpy
        from pathlib import Path
        configuration = Path(__file__).resolve().parent.parent / "config" / "settings.py"
        with patch.dict(os.environ, {"DJANGO_SECRET_KEY": "configuration-test", **values}, clear=True):
            return runpy.run_path(str(configuration))["DATABASES"]["default"]

    def test_bundled_database_defaults(self):
        database = self.database_settings()
        self.assertEqual(database["HOST"], "db")
        self.assertEqual(database["NAME"], "nsx")
        self.assertEqual(database["OPTIONS"], {"sslmode": "prefer", "connect_timeout": 10})

    def test_remote_connection_and_tls_settings(self):
        database = self.database_settings(POSTGRES_HOST="db.customer.example", POSTGRES_PORT="5433",
            POSTGRES_DB="customer_nsx", POSTGRES_USER="customer_reader", POSTGRES_PASSWORD="test-password",
            POSTGRES_SSLMODE="verify-full", POSTGRES_SSLROOTCERT="/certificates/customer.pem",
            POSTGRES_CONNECT_TIMEOUT="15")
        self.assertEqual(database["HOST"], "db.customer.example")
        self.assertEqual(database["PORT"], "5433")
        self.assertEqual(database["NAME"], "customer_nsx")
        self.assertEqual(database["USER"], "customer_reader")
        self.assertEqual(database["PASSWORD"], "test-password")
        self.assertEqual(database["OPTIONS"], {"sslmode": "verify-full", "connect_timeout": 15,
                                              "sslrootcert": "/certificates/customer.pem"})
        self.assertTrue(database["CONN_HEALTH_CHECKS"])

    def test_sqlite_development_option_stays_explicit(self):
        database = self.database_settings(NSX_SQLITE_PATH=":memory:", POSTGRES_SSLROOTCERT="system")
        self.assertEqual(database, {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"})
