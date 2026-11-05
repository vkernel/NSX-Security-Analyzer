from unittest.mock import Mock
from django.contrib.auth import get_user_model
from django.db import connection
from django.http import Http404
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from . import collector
from .group_memberships import read
from .models import Environment, Snapshot, SnapshotRecord
from .resolved_membership import collect, vm_links


class MemberCollectionTests(SimpleTestCase):
    group = {'path': '/infra/domains/default/groups/web'}

    def nsx_client(self, pages):
        client = Mock()
        client.get.side_effect = lambda path, params: (next(pages) if path.endswith('virtual-machines')
            else {'results': [], 'result_count': 0})
        return client

    def test_follows_pages_and_saves_only_identity_fields(self):
        client = self.nsx_client(iter([{'results': [{'external_id': 'a', 'display_name': 'A', 'tags': ['not copied']}], 'result_count': 2, 'cursor': 'opaque'},
                                   {'results': [{'external_id': 'b', 'display_name': 'B'}], 'result_count': 2}]))
        result = collect(client, self.group)['endpoints']['virtual-machines']
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([row['identity'] for row in result['items']], ['a', 'b'])
        self.assertNotIn('tags', result['items'][0])
        self.assertEqual(client.get.call_args_list[1].args[1]['cursor'], 'opaque')

    def test_reuses_complete_existence_pages_without_repeating_requests(self):
        client = self.nsx_client(iter([]))
        probe = collector.MembershipProbe(client)
        probe.pages = {key: {'results': [], 'result_count': 0} for key in collector.MEMBERSHIP_ENDPOINTS}
        result = collect(client, self.group, probe=probe)
        client.get.assert_not_called()
        self.assertTrue(all(row['status'] == 'complete' for row in result['endpoints'].values()))

    def test_failed_later_page_keeps_positive_members_and_partial_status(self):
        client = self.nsx_client(iter([{'results': [{'external_id': 'a'}], 'result_count': 2, 'cursor': 'next'}]))
        original = client.get.side_effect
        def get(path, params):
            if params.get('cursor'):
                raise collector.AuditError('permission denied', status_code=403)
            return original(path, params)
        client.get.side_effect = get
        result = collect(client, self.group)['endpoints']['virtual-machines']
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['items']), 1)
        self.assertIn('403', result['notes'][0])

    def test_duplicate_changed_total_and_cursor_loop_never_establish_complete(self):
        for pages in ([{'results': [{'id': 'a'}], 'result_count': 2, 'cursor': 'same'},
                       {'results': [{'id': 'a'}], 'result_count': 2, 'cursor': 'same'}],
                      [{'results': [{'id': 'a'}], 'result_count': 3, 'cursor': 'next'},
                       {'results': [{'id': 'b'}], 'result_count': 2}]):
            result = collect(self.nsx_client(iter(pages)), self.group)['endpoints']['virtual-machines']
            self.assertEqual(result['status'], 'partial')
            self.assertTrue(result['notes'])

    def test_limits_and_testing_keep_absence_unknown(self):
        result = collect(self.nsx_client(iter([{'results': [{'id': 'a'}, {'id': 'b'}], 'result_count': 10, 'cursor': 'next'}])), self.group, limit=1)['endpoints']['virtual-machines']
        self.assertEqual(result['status'], 'partial'); self.assertEqual(len(result['items']), 1)
        result = collect(self.nsx_client(iter([{'results': [], 'result_count': 0}])), self.group, testing=True)['endpoints']['virtual-machines']
        self.assertEqual(result['status'], 'partial')

    def test_first_page_denied_and_malformed_do_not_look_empty(self):
        for value, status in ((collector.AuditError('denied', status_code=403), 'unavailable'),
                              (collector.AuditError('failed state', status_code=400), 'unavailable'),
                              (collector.AuditError('unsupported', status_code=501), 'not_supported'),
                              ({'results': [{'display_name': 'name alone'}]}, 'unavailable')):
            client = Mock()
            client.get.side_effect = value if isinstance(value, Exception) else None
            client.get.return_value = value
            result = collect(client, self.group)['endpoints']['virtual-machines']
            self.assertEqual(result['status'], status)

    def test_malformed_probe_does_not_crash_and_denied_probes_are_not_repeated(self):
        client = Mock()
        client.get.return_value = {'results': None, 'result_count': 0}
        probe = collector.MembershipProbe(client)
        probe.pages = {key: client.get.return_value for key in collector.MEMBERSHIP_ENDPOINTS}
        result = collect(client, self.group, probe=probe)
        self.assertTrue(all(row['status'] == 'unavailable' for row in result['endpoints'].values()))
        client.get.reset_mock()
        probe.failures = {key: collector.AuditError('denied', status_code=403) for key in collector.MEMBERSHIP_ENDPOINTS}
        result = collect(client, self.group, probe=probe)
        client.get.assert_not_called()
        self.assertTrue(all(row['status'] == 'unavailable' for row in result['endpoints'].values()))

    def test_exact_identity_links_ignore_tags_names_and_ambiguous_aliases(self):
        tags = {'vm_inventory_complete': True, 'virtual_machines': [
            {'path': '/vm/a', 'external_id': 'a', 'display_name': 'Same name'},
            {'path': '/vm/b', 'external_id': 'b', 'display_name': 'Same name'}]}
        group = {'path': '/g', 'name': 'Web', 'resolved_membership': {'endpoints': {'virtual-machines':
            {'status': 'complete', 'items': [{'external_id': 'a', 'identity': 'a'}, {'id': 'unknown', 'name': 'Same name'}]}}}}
        links, status = vm_links(tags, [group], {'version': 1})
        self.assertEqual(status, 'partial')  # The unknown member cannot be joined to VM inventory.
        self.assertEqual(list(links), ['/vm/a'])
        self.assertEqual(group['resolved_membership']['endpoints']['virtual-machines']['items'][0]['vm_path'], '/vm/a')
        tags['virtual_machines'].append({'path': '/vm/conflict', 'external_id': 'a'})
        links, status = vm_links(tags, [group], {'version': 1})
        self.assertFalse(links)
        self.assertEqual(status, 'partial')

    def test_realized_and_fabric_vm_identities_link_without_name_matching(self):
        tags = {'vm_inventory_complete': True, 'virtual_machines': [
            {'path': '/inventory/vm', 'id': 'inventory-id', 'compute_ids': ['externalId:fabric-id', 'hostLocalId:1']}]}
        member = {'path': '/enforcement/vm', 'id': 'realized-id', 'compute_ids': ['externalId:fabric-id'], 'identity': 'realized-id'}
        group = {'name': 'Web', 'path': '/g', 'resolved_membership': {'endpoints': {'virtual-machines': {'status': 'complete', 'items': [member]}}}}
        links, status = vm_links(tags, [group], {'version': 1})
        self.assertEqual(list(links), ['/inventory/vm']); self.assertEqual(status, 'complete')
        member['compute_ids'] = ['hostLocalId:1']
        links, _ = vm_links(tags, [group], {'version': 1})
        self.assertFalse(links)

    def test_tag_and_resolved_relationships_are_independent(self):
        tags = {'vm_inventory_complete': True, 'virtual_machines': [{'path': '/vm', 'external_id': 'id', 'tags': []}],
            'objects': [{'name': 'prod', 'vms': {'/vm': 'VM'}, 'group_conditions': {'/wrong-tag-match': 'Unmatched AND group'}}]}
        group = {'name': 'Explicit VM group', 'path': '/explicit', 'resolved_membership': {'endpoints': {'virtual-machines':
            {'status': 'complete', 'items': [{'external_id': 'id', 'identity': 'id'}]}}}}
        row = next(collector.iter_vm_inventory_rows({'tags': tags, 'inventory': {'groups': [group]}, 'membership_evidence': {'version': 1}}))
        self.assertEqual([r['path'] for r in row['resolved_groups']], ['/explicit'])
        self.assertEqual([r['path'] for r in row['related_groups']], ['/wrong-tag-match'])
        self.assertEqual(row['resolved_group_count'], 1)
        row = next(collector.iter_vm_inventory_rows({'tags': tags}))
        self.assertIsNone(row['resolved_group_count'])
        self.assertEqual(row['resolved_membership_status'], 'not_collected')


class MemberReadTests(TestCase):
    def setUp(self):
        env = Environment.objects.create(name='Members', slug='members', manager='https://offline.invalid')
        self.snapshot = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={})
        self.path = '/infra/domains/default/groups/web'
        self.group = SnapshotRecord.objects.create(snapshot=self.snapshot, ordinal=1, view='inventory', name='Web',
            compact={'path': self.path}, data={'kind': 'group', 'path': self.path, 'resolved_membership': {'version': 1,
                'checked_at': timezone.now().isoformat(), 'endpoints': {'virtual-machines': {'status': 'partial', 'reported_count': 100,
                    'notes': ['Page failed'], 'items': [{'identity': str(i), 'external_id': str(i), 'name': 'VM '+str(i), 'vm_path': '/vm/'+str(i)} for i in range(53)]}}}}, columns=[], sort_values={})

    def test_members_are_bounded_with_coverage_and_no_full_report_read(self):
        with CaptureQueriesContext(connection) as queries:
            result = read(self.snapshot.pk, self.path)
        self.assertEqual(len(result['items']), 25); self.assertTrue(result['has_next'])
        self.assertEqual((result['status'], result['saved_count'], result['reported_count']), ('partial', 53, 100))
        self.assertEqual(len(read(self.snapshot.pk, self.path, page=2)['items']), 3)
        self.assertEqual(len(queries), 2)
        self.assertFalse(any('"report"' in q['sql'] for q in queries))
        self.assertTrue(any('LIMIT 26' in q['sql'] for q in queries))

    def test_legacy_missing_and_unsupported_are_distinct_from_zero(self):
        self.assertEqual(read(self.snapshot.pk, self.path, 'ip-addresses')['status'], 'not_collected')
        for status in ('complete', 'unavailable', 'not_supported', 'configured', 'not_applicable'):
            self.group.data['resolved_membership']['endpoints']['ip-addresses'] = {'status': status, 'items': []}
            self.group.save()
            result = read(self.snapshot.pk, self.path, 'ip-addresses')
            self.assertEqual(result['status'], status); self.assertEqual(result['items'], [])

    def test_snapshot_kind_and_pagination_validation(self):
        other = Snapshot.objects.create(environment=self.snapshot.environment, generated_at=timezone.now(), report={})
        with self.assertRaises(Http404): read(other.pk, self.path)
        with self.assertRaises(ValueError): read(self.snapshot.pk, self.path, page=-1)
        with self.assertRaises(ValueError): read(self.snapshot.pk, self.path, 'invented')
        self.group.data['kind'] = 'custom_service'; self.group.save()
        with self.assertRaises(Http404): read(self.snapshot.pk, self.path)

    def test_api_login_archive_and_detail_stays_small(self):
        url = reverse('snapshot-data', args=[self.snapshot.pk])
        params = {'op': 'group-members', 'path': self.path}
        self.assertEqual(self.client.get(url, params).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user('member-reader'))
        self.assertEqual(len(self.client.get(url, params).json()['items']), 25)
        detail = self.client.get(url, {'op': 'detail', 'id': 1}).json()
        self.assertNotIn('resolved_membership', detail['data'])
        self.snapshot.browsing_status = 'archived'; self.snapshot.save()
        self.assertEqual(self.client.get(url, params).status_code, 409)

    def test_vm_summary_and_pages_never_upgrade_legacy_tag_counts(self):
        from .vm_relationships import read as vm_read
        vm = SnapshotRecord.objects.create(snapshot=self.snapshot, ordinal=2, view='vms', name='VM',
            compact={'group_count': 999}, data={'related_groups': [{'path': '/tag-group'}]}, columns=[], sort_values={})
        self.assertIsNone(vm_read(self.snapshot.pk, 2)['data']['resolved_group_count'])
        self.assertEqual(vm_read(self.snapshot.pk, 2, 'resolved_groups')['status'], 'not_collected')
        vm.compact.update(membership_evidence_version=1, resolved_group_count=1, resolved_membership_status='partial')
        vm.data['resolved_groups'] = [{'path': self.path, 'name': 'Web', 'status': 'partial'}]; vm.save()
        result = vm_read(self.snapshot.pk, 2, 'resolved_groups')
        self.assertEqual(result['status'], 'partial'); self.assertEqual(result['items'][0]['path'], self.path)

    def test_table_sort_filter_values_and_export_use_resolved_counts(self):
        import csv, io, json
        from .models import SnapshotPanel, SnapshotRecordPanel
        panel = SnapshotPanel.objects.create(snapshot=self.snapshot, slug='all-vms', html='')
        for ordinal, count in ((10, 2), (11, None), (12, None)):
            data = {'name': str(ordinal), 'path': '/vm/'+str(ordinal), 'power_state': 'VM_RUNNING', 'group_count': 999}
            if count is not None:
                data.update(membership_evidence_version=1, resolved_membership_status='complete', resolved_group_count=count)
            elif ordinal == 12:
                data.update(membership_evidence_version=1, resolved_membership_status='unavailable', resolved_group_count=None)
            vm = SnapshotRecord.objects.create(snapshot=self.snapshot, ordinal=ordinal, view='vms', name=str(ordinal),
                compact=data, data=data, sort_values={'group_count': 999, 'resolved_group_count': count},
                columns=[str(ordinal), 'VM_RUNNING', '2', '999', None])
            SnapshotRecordPanel.objects.create(record=vm, panel=panel)
        self.client.force_login(get_user_model().objects.create_user('table-member-reader'))
        url = reverse('snapshot-data', args=[self.snapshot.pk])
        params = {'panel': 'all-vms', 'sort': 'resolved_group_count', 'order': 'desc'}
        rows = self.client.get(url, params).json()['rows']
        self.assertEqual([r['id'] for r in rows], [10, 11, 12])
        self.assertIsNone(rows[1]['data']['resolved_group_count'])
        filtered = self.client.get(url, dict(params, filters=json.dumps([[3, {'text': '2'}]]))).json()
        self.assertEqual([r['id'] for r in filtered['rows']], [10])
        legacy = self.client.get(url, dict(params, filters=json.dumps([[3, {'text': 'Not collected'}]]))).json()
        self.assertEqual([r['id'] for r in legacy['rows']], [11])
        unavailable = self.client.get(url, dict(params, filters=json.dumps([[3, {'text': 'Unavailable'}]]))).json()
        self.assertEqual([r['id'] for r in unavailable['rows']], [12])
        suggestions = self.client.get(url, dict(params, op='values', column=3)).json()
        self.assertEqual(suggestions['values'], [{'value': '2', 'count': 1}, {'value': 'Not collected', 'count': 1}, {'value': 'Unavailable', 'count': 1}])
        response = self.client.get(url, dict(params, op='export'))
        exported = list(csv.reader(io.StringIO(b''.join(response.streaming_content).decode('utf-8-sig'))))
        self.assertEqual(exported[0][3], 'Resolved groups')
        self.assertEqual([r[3] for r in exported[1:]], ['2', 'Not Collected', 'Unavailable'])
