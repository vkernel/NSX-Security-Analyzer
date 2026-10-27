from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import EnvironmentForm
from .models import Environment
from .services import claim_job, enqueue, execute_job, engine
from .tests import sample_report


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CollectionSettingsTests(TestCase):
    def data(self, **changes):
        data = dict(name='East', slug='east', manager='east.example', username='reader',
                    password='test-password', timeout=30, retries=2, enabled=True,
                    sync_interval_minutes=60, max_concurrent_requests=12)
        return {**data, **changes}

    def test_gui_saves_limit_and_rejects_invalid_values(self):
        user = get_user_model().objects.create_user('settings-admin', is_staff=True)
        self.client.force_login(user)
        response = self.client.get(reverse('environment-new'))
        self.assertContains(response, 'Maximum concurrent NSX requests')
        self.assertContains(response, 'name="max_concurrent_requests"')
        for value in (0, 33, '1.5', 'invalid'):
            form = EnvironmentForm(self.data(max_concurrent_requests=value))
            self.assertFalse(form.is_valid())
            self.assertIn('max_concurrent_requests', form.errors)
        self.assertEqual(self.client.post(reverse('environment-new'), self.data()).status_code, 302)
        environment = Environment.objects.get()
        self.assertEqual(environment.max_concurrent_requests, 12)
        self.assertEqual(Environment().max_concurrent_requests, 8)
        form = EnvironmentForm(self.data(max_concurrent_requests='', password=''), instance=environment)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().max_concurrent_requests, 12)

    def test_worker_uses_queued_limit_and_legacy_jobs_default_to_eight(self):
        form = EnvironmentForm(self.data(max_concurrent_requests=16))
        self.assertTrue(form.is_valid(), form.errors)
        environment = form.save()
        for legacy, expected in ((False, 16), (True, 8)):
            job = enqueue(environment, None)
            if legacy:
                job.config.pop('max_concurrent_requests')
                job.save(update_fields=['config'])
            Environment.objects.filter(pk=environment.pk).update(max_concurrent_requests=4)
            claim_job()
            audit = engine()
            with patch.object(audit, 'audit', return_value=sample_report()) as collect:
                execute_job(job.pk)
            job.refresh_from_db()
            self.assertEqual(job.status, 'succeeded', job.error)
            self.assertEqual(collect.call_args.kwargs['workers'], expected)
            self.assertEqual(job.snapshot.report['performance']['collector_build']['configured_maximum'], expected)
