from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, SnapshotComparison, SnapshotRecord
from .tests import sample_report
from .findings import synchronize
from .snapshot_index import build


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class EnvironmentPerformanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('performance', is_staff=True)
        self.client.force_login(self.user)
        self.env = Environment.objects.create(name='Performance', slug='performance', manager='https://east.example')
        self.before = Snapshot.objects.create(environment=self.env, generated_at=timezone.now()-timedelta(hours=1), report=sample_report())
        report = sample_report()
        report['objects'][0]['name'] = 'Updated group'
        self.after = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report=report)

    def test_comparison_tab_does_not_compare_on_navigation(self):
        with patch('inventory.comparison_cache.compare', side_effect=AssertionError('Unexpected comparison')), CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('snapshot-comparison', args=[self.env.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SnapshotComparison.objects.exists())
        self.assertFalse(any('"report"' in q['sql'] for q in queries))

    def test_comparison_is_shared_and_paged_without_reloading_reports(self):
        url = reverse('snapshot-comparison', args=[self.env.pk])
        params = {'before':self.before.pk, 'after':self.after.pk}
        self.assertContains(self.client.get(url, params), 'Updated group')
        with patch('inventory.comparison_cache.compare', side_effect=AssertionError('Recomputed pair')), CaptureQueriesContext(connection) as queries:
            response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any('"report"' in q['sql'] for q in queries))
        self.assertTrue(any('inventory_snapshotcomparisonrow' in q['sql'] and 'LIMIT' in q['sql'] for q in queries))
        self.after.report['objects'][0]['name'] = 'Edited evidence'
        self.after.save(update_fields=['report'])
        self.assertFalse(SnapshotComparison.objects.exists())
        self.assertContains(self.client.get(url, params), 'Edited evidence')

    def test_findings_reads_do_not_synchronize_or_lock(self):
        synchronize(self.env.pk)
        from .models import Finding
        finding = Finding.objects.filter(environment=self.env).first()
        # sample_report may not produce a finding until membership is unknown.
        if finding is None:
            self.after.report['objects'][0]['membership'] = 'unknown'
            self.after.save(update_fields=['report'])
            synchronize(self.env.pk)
            finding = Finding.objects.get(environment=self.env)
        for name,args in [('findings',[self.env.pk]),('finding-detail',[self.env.pk,finding.pk])]:
            with patch('inventory.findings.synchronize', side_effect=AssertionError('Synchronized on GET')), CaptureQueriesContext(connection) as queries:
                response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(any('FOR UPDATE' in q['sql'] or '"report"' in q['sql'] for q in queries))

    def test_coverage_fetches_only_current_issue_page(self):
        report = sample_report()
        report['objects'] = [dict(report['objects'][0], path=f'/groups/{i}', name=f'Unknown {i}', membership='unknown', notes=['Unresolved']) for i in range(80)]
        self.after.report = report
        self.after.save(update_fields=['report'])
        build(self.after)
        from .coverage import dashboard
        from django.core.paginator import Paginator
        with CaptureQueriesContext(connection) as queries:
            result = dashboard(self.env, 30, lazy=True)
            page = Paginator(result['issues'], 10).get_page(2)
            rows = list(page)
        self.assertEqual(len(rows), 10)
        selects = [q['sql'] for q in queries if 'inventory_snapshotrecord' in q['sql'] and 'notes' in q['sql']]
        self.assertEqual(len(selects), 1)
        self.assertIn('LIMIT 10 OFFSET 10', selects[0])
        eager = dashboard(self.env, 30)['issues']
        self.assertEqual(rows, eager[10:20])

    def test_selector_is_bounded_and_keeps_explicit_old_selection(self):
        from .forms import SnapshotComparisonForm
        Snapshot.objects.bulk_create([Snapshot(environment=self.env, generated_at=timezone.now()+timedelta(minutes=i), report={}) for i in range(105)])
        form = SnapshotComparisonForm(environment=self.env)
        self.assertEqual(form.fields['before'].queryset.count(),100)
        form = SnapshotComparisonForm({'before':self.before.pk,'after':self.after.pk}, environment=self.env)
        self.assertTrue(form.is_valid(),form.errors)
        self.assertLessEqual(form.fields['before'].queryset.count(),102)
