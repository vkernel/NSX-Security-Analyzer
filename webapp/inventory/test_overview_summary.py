"""Inventory overview summary reads saved projections only."""
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (Environment, Snapshot, SnapshotComparison, SnapshotComparisonRow, SnapshotCoverage,
                     SnapshotCoverageIssue)
from .overview_summary import inventory_overview
from .test_foundations import report


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class InventoryOverviewTests(TestCase):
    def setUp(self):
        self.env = Environment.objects.create(name='Overview', slug='overview-env', manager='https://overview.invalid')
        now = timezone.now()
        summary = {'groups': 12, 'services': 4, 'rules': 9, 'policies': 1, 'tags': 7}
        self.previous = Snapshot.objects.create(environment=self.env, generated_at=now - timedelta(days=1), report={}, summary=summary)
        self.latest = Snapshot.objects.create(environment=self.env, generated_at=now, report=report(), summary=summary)

    def test_counts_and_missing_coverage_stays_unknown(self):
        data = inventory_overview(self.latest)
        counts = {row['label']: row['value'] for row in data['counts']}
        self.assertEqual(counts['Groups'], 12)
        self.assertEqual(counts['Firewall rules'], 9)
        self.assertIsNone(counts['VMs'])  # older summaries without a VM count: not recorded, not zero
        self.assertFalse(data['coverage']['recorded'])
        self.assertEqual({row['state'] for row in data['coverage']['rows']}, {'Not recorded'})

    def test_coverage_issues_are_grouped_and_unknown_areas_kept(self):
        saved = SnapshotCoverage.objects.create(snapshot=self.latest, issue_count=3)
        for ordinal, area in enumerate(['Network references', 'Rule counters', 'Something new']):
            SnapshotCoverageIssue.objects.create(coverage=saved, ordinal=ordinal, area=area, name='x', detail='y')
        rows = {row['area']: row for row in inventory_overview(self.latest)['coverage']['rows']}
        self.assertEqual(rows['Interface attachments']['state'], 'Needs review')
        self.assertEqual(rows['Firewall policies and rules']['detail'], '1 issue')
        self.assertEqual(rows['Group membership']['state'], 'Complete')
        self.assertEqual(rows['Other evidence']['state'], 'Needs review')

    def test_changes_only_from_a_calculated_comparison(self):
        data = inventory_overview(self.latest)['changes']
        self.assertEqual(data['previous'], self.previous)
        self.assertFalse(data['ready'])
        self.assertFalse(SnapshotComparison.objects.exists())
        comparison = SnapshotComparison.objects.create(before=self.previous, after=self.latest, ready=True, status='ready')
        for ordinal, (kind, status) in enumerate([('Group', 'Added'), ('Group', 'Changed'), ('Rule', 'Removed'), ('Rule', 'Presence uncertain')]):
            SnapshotComparisonRow.objects.create(comparison=comparison, ordinal=ordinal, data={'kind': kind, 'status': status})
        kinds = {row['label']: row for row in inventory_overview(self.latest)['changes']['kinds']}
        self.assertEqual((kinds['Groups']['added'], kinds['Groups']['changed']), (1, 1))
        self.assertEqual((kinds['Firewall rules']['removed'], kinds['Firewall rules']['uncertain']), (1, 1))
        self.assertIsNone(inventory_overview(self.previous)['changes']['previous'])

    def test_snapshot_page_renders_summary_without_starting_a_comparison(self):
        user = get_user_model().objects.create_user('overview-viewer', password='synthetic-only')
        self.client.force_login(user)
        shell = {'content': '<section id="overview"></section>', 'styles': '', 'scripts': ''}
        with patch('inventory.report_cache.presentation', return_value=(shell, {})):
            response = self.client.get(reverse('snapshot', args=[self.latest.pk]), {'browse': 1})
        self.assertContains(response, 'id="inventory-overview-summary"')
        self.assertContains(response, 'Changes since the previous snapshot')
        self.assertContains(response, 'Not compared yet')
        self.assertFalse(SnapshotComparison.objects.exists())
