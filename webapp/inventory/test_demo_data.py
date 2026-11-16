"""Synthetic demonstration data: real collector pipeline, no network, safe credentials."""
import io
import os
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .demo_data import scenario
from .demo_data.transport import DemoNSXClient, FakePool, SyntheticManager, no_network
from .models import AuditJob, DeletionBatch, Environment, Finding, NamingPolicy, Snapshot

SMALL = scenario.Profile(
    'production', 'Production (demo)', 'nsx-production.example.invalid', 'production', 'PRD', 'prd', 10,
    (('orion', 3), ('lyra', 2), ('vega', 2), ('atlas', 2), ('nova', 2)),
    (34, 27, 20, 13, 6, 0.08), partial_index=2, failures=((23.5, 'unauthorized'),), legacy_groups=8,
    special_groups=True)
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}


def credentials(path):
    rows = [line.split('\t') for line in open(path).read().splitlines() if line and not line.startswith('#')]
    return {row[0]: (row[1], row[2]) for row in rows[1:]}


class SyntheticManagerTests(SimpleTestCase):
    def setUp(self):
        self.inventory = scenario.build(SMALL, 5, 6, datetime.now(timezone.utc))
        self.manager = SyntheticManager(self.inventory)

    def test_segment_groups_have_ports_but_no_direct_vm_members(self):
        group = self.inventory.segment_groups['web']
        status, vms = self.manager.handle(group + '/members/virtual-machines', {'page_size': '1000'})
        self.assertEqual((status, vms['results']), (200, []))
        status, ports = self.manager.handle(group + '/members/segment-ports', {'page_size': '1000'})
        self.assertTrue(ports['results'])
        self.assertTrue(all('/segments/prd-web-segment/ports/' in row['path'] for row in ports['results']))
        tagged = scenario.GROUPS + 'PRD-ORION-WEB'
        names = [row['display_name'] for row in self.manager.handle(tagged + '/members/virtual-machines', {})[1]['results']]
        self.assertTrue(names and all(name.startswith('prd-orion-web-') for name in names))

    def test_pagination_and_partial_inventory(self):
        status, first = self.manager.handle('/infra/domains/default/groups', {'page_size': '10'})
        self.assertEqual((len(first['results']), first['result_count'], first['cursor']), (10, len(self.inventory.groups), '10'))
        partial = SyntheticManager(scenario.build(SMALL, 2, 6, datetime.now(timezone.utc)))
        page = partial.handle('/api/v1/fabric/vifs', {'page_size': '1000'})[1]
        self.assertGreater(page['result_count'], len(page['results']))

    def test_client_only_answers_the_synthetic_manager_and_blocks_sockets(self):
        client = DemoNSXClient(SMALL.manager, 'reader', 'not-a-secret', self.manager, retries=0)
        self.assertIsInstance(client.http, FakePool)
        self.assertEqual(client.get('/infra/domains')['results'][0]['path'], '/infra/domains/default')
        with self.assertRaises(Exception):
            client.http.request('GET', 'https://nsx.example.com/policy/api/v1/infra/domains')
        import socket
        with no_network(), self.assertRaises(OSError):
            socket.create_connection(('192.0.2.1', 443), timeout=1)


@override_settings(STORAGES=STORAGES)
class LoadDemoDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.directory = tempfile.mkdtemp()
        cls.path = os.path.join(cls.directory, 'demo-credentials.txt')
        cls.output = io.StringIO()
        with patch('inventory.demo_data.loader.PROFILES', (SMALL,)):
            call_command('load_demo_data', credentials_file=cls.path, stdout=cls.output)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.directory, ignore_errors=True)

    def test_collections_are_full_published_snapshots_with_history(self):
        environment = Environment.objects.get(slug='demo-production')
        self.assertFalse(environment.enabled)
        self.assertTrue(environment.manager.endswith('.example.invalid'))
        snapshots = list(environment.snapshots.order_by('generated_at'))
        self.assertEqual(len(snapshots), 6)
        self.assertTrue(all(not s.testing and not s.imported and s.browsing_status == 'ready' for s in snapshots))
        self.assertGreater((snapshots[-1].generated_at - snapshots[0].generated_at).days, 30)
        self.assertEqual([s.summary['inventory_warnings'] for s in snapshots].count(True), 1)
        self.assertTrue(AuditJob.objects.filter(environment=environment, status='failed', error__contains='403').exists())
        report = snapshots[-1].report
        self.assertEqual(report['manager'], 'nsx-production.example.invalid')
        self.assertEqual(report['search_coverage']['mode'], 'all_types')
        rules = report['dfw']['rules']
        self.assertTrue(any(r['configuration']['rule']['sources_excluded'] for r in rules))
        self.assertTrue(any(r['configuration']['rule']['destinations_excluded'] for r in rules))
        self.assertTrue(any(r['configuration']['policy']['scope'] != ['ANY'] for r in rules))
        self.assertTrue(any(r['disabled'] for r in rules))
        self.assertEqual(snapshots[2].report['membership_evidence']['network']['status'], 'partial')

    def test_direct_and_network_membership_stay_distinct(self):
        report = Environment.objects.get(slug='demo-production').snapshots.first().report
        segment = scenario.GROUPS + 'PRD-SEG-WEB'
        direct = {item['path'] for record in report['membership_evidence']['vms'].values() for item in record['items']}
        network = {item['path']: item['membership_basis'] for record in report['membership_evidence']['network']['vms'].values()
                   for item in record['items']}
        self.assertNotIn(segment, direct)
        self.assertEqual(network[segment], 'network_attachment')
        self.assertIn(scenario.GROUPS + 'PRD-ORION-WEB', direct)

    def test_review_workflow_stages_and_cancelled_preview(self):
        states = set(Finding.objects.values_list('workflow_state', flat=True))
        self.assertTrue({'unassigned', 'owner_review', 'second_review', 'ready', 'decommissioned', 'rejected'} <= states)
        self.assertTrue(Finding.objects.filter(qualification='observing').exists() or
                        Finding.objects.filter(qualification='insufficient').exists())
        batch = DeletionBatch.objects.get()
        self.assertEqual((batch.status, batch.items.get().status), ('cancelled', 'cancelled'))
        self.assertEqual(NamingPolicy.objects.filter(name__startswith='Demo ', enabled=True).count(), 2)

    def test_credentials_are_private_and_never_printed(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        accounts = credentials(self.path)
        self.assertEqual({role for role, _ in accounts.values()}, {'admin', 'operator', 'viewer'})
        self.assertEqual(len(accounts), 5)
        output = self.output.getvalue()
        for username, (role, password) in accounts.items():
            user = get_user_model().objects.get(username=username)
            self.assertTrue(user.check_password(password))
            self.assertNotIn(password, output)
            self.assertGreaterEqual(len(password), 20)
        self.assertEqual(get_user_model().objects.get(username='demo-admin').is_superuser, True)
        self.assertEqual(get_user_model().objects.get(username='demo-viewer').is_staff, False)

    def test_pages_render_for_demo_accounts(self):
        environment = Environment.objects.get(slug='demo-production')
        snapshots = list(environment.snapshots.all())
        finding = Finding.objects.filter(workflow_state='second_review').first()
        urls = ['/', reverse('environment', args=[environment.pk]), reverse('snapshot', args=[snapshots[0].pk]),
                reverse('all-collections'), reverse('activity'), reverse('findings', args=[environment.pk]),
                reverse('finding-detail', args=[environment.pk, finding.pk]),
                reverse('naming-compliance', args=[environment.pk]),
                reverse('snapshot-comparison', args=[environment.pk]) + f'?before={snapshots[-1].pk}&after={snapshots[0].pk}']
        urls += [reverse('my-work') + '?queue=' + queue for queue in ('assigned', 'second', 'unassigned', 'ready')]
        self.client.force_login(get_user_model().objects.get(username='demo-admin'))
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.get(reverse('segmentation', args=[environment.pk]), follow=True)
        self.assertContains(response, 'orion')

    def test_refuses_existing_data_and_unsafe_credentials_paths(self):
        other = os.path.join(self.directory, 'second.txt')
        with self.assertRaisesMessage(CommandError, '--reset'):
            call_command('load_demo_data', credentials_file=other, stdout=io.StringIO())
        with self.assertRaisesMessage(CommandError, 'outside the application source tree'):
            call_command('load_demo_data', credentials_file=os.path.join(settings.BASE_DIR, 'x.txt'), reset=True)
        Environment.objects.create(slug='customer', name='Customer', manager='https://nsx.example.com')
        with self.assertRaisesMessage(CommandError, '--force'):
            call_command('load_demo_data', credentials_file=other, reset=True, stdout=io.StringIO())
        self.assertFalse(os.path.exists(other))

    def test_reset_replaces_only_demonstration_data(self):
        keep = Environment.objects.create(slug='customer', name='Customer', manager='https://nsx.example.com')
        before = credentials(self.path)
        with patch('inventory.demo_data.loader.PROFILES', (SMALL,)):
            call_command('load_demo_data', credentials_file=self.path, reset=True, force=True, stdout=io.StringIO())
        self.assertTrue(Environment.objects.filter(pk=keep.pk).exists())
        self.assertEqual(Environment.objects.filter(slug='demo-production').count(), 1)
        self.assertEqual(Snapshot.objects.count(), 6)
        after = credentials(self.path)
        self.assertNotEqual(before['demo-admin'][1], after['demo-admin'][1])
        # With another environment present, naming policies are scoped to demo environments.
        self.assertFalse(NamingPolicy.objects.filter(name__startswith='Demo ', environment__isnull=True).exists())
