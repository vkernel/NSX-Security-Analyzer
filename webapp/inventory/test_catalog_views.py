"""Services and tag scopes overviews from saved snapshot records."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .catalog_views import describe, reference_objects, scopes, services
from .models import Environment, Snapshot, SnapshotRecord


class CatalogViewTests(TestCase):
    def setUp(self):
        env = Environment.objects.create(name='Catalog', slug='catalog', manager='https://catalog.invalid')
        self.snapshot = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={})
        self.ordinal = 0
        self.service('HTTPS', [{'resource_type': 'L4PortSetServiceEntry', 'l4_protocol': 'TCP', 'destination_ports': ['443']}], 'not_assessed', [], default=True)
        self.service('svc-wide', [{'resource_type': 'L4PortSetServiceEntry', 'l4_protocol': 'TCP', 'destination_ports': ['1024-65535']}], 'referenced',
                     ['/infra/domains/default/security-policies/p/rules/a', '/infra/domains/default/groups/g'])
        self.service('svc-old', [{'resource_type': 'L4PortSetServiceEntry', 'l4_protocol': 'UDP', 'destination_ports': ['9100']}], 'unused_candidate', [])
        self.service('svc-bundle', [{'resource_type': 'NestedServiceServiceEntry', 'nested_service_path': '/infra/services/HTTPS'}], 'referenced', [])
        self.scope('app', 3)
        self.tag('app', 'web', 4, 2, 'both'); self.tag('app', 'Web', 1, 0, 'vm_only'); self.tag('app', 'reporting', 0, 2, 'group_only')
        self.tag('env', 'prd', 3, 1, 'both')

    def add(self, view, name, data):
        self.ordinal += 1
        return SnapshotRecord.objects.create(snapshot=self.snapshot, ordinal=self.ordinal, view=view, name=name, sort_name=name.casefold(),
                                             compact={'name': name, 'path': data.get('path', name)}, data=data, columns=[], sort_values={},
                                             search_basic=name, search_evidence='')

    def service(self, name, entries, usage, refs, default=False):
        self.add('inventory', name, {'kind': 'custom_service', 'name': name, 'path': '/infra/services/' + name, 'usage': usage, 'referenced_by': refs,
                                     'inventory_type': 'Built-in service' if default else 'Custom service',
                                     'configuration': {'is_default': default, 'service_entries': entries}})

    def scope(self, name, values):
        self.add('scopes', name, {'name': name, 'scope': name, 'tag_count': values, 'vm_count': 5, 'group_count': 4, 'other_count': 0})

    def tag(self, scope, value, vms, groups, status):
        self.add('tags', value, {'name': value, 'scope': scope, 'vm_count': vms, 'group_count': groups, 'other_count': 0, 'status': status})

    def test_service_entries_observations_and_views(self):
        data = services(self.snapshot.pk)
        self.assertEqual(data['totals'], {'total': 4, 'predefined': 1, 'unreferenced': 1, 'custom': 3})
        rows = {item['name']: item for item in data['items']}
        self.assertEqual(rows['HTTPS']['entries'], ['TCP 443'])
        self.assertIsNone(rows['HTTPS']['rules'])                 # built-in: not assessed, not zero
        self.assertEqual(rows['svc-wide']['rules'], 1)
        self.assertIn('Broad port range', rows['svc-wide']['notes'])
        self.assertEqual(rows['svc-old']['notes'], ['Not referenced'])
        self.assertEqual((rows['svc-bundle']['entries'], rows['svc-bundle']['notes']), (['HTTPS'], ['Nested service']))
        self.assertEqual([i['name'] for i in services(self.snapshot.pk, view='unreferenced')['items']], ['svc-old'])
        self.assertEqual(services(self.snapshot.pk, view='custom')['count'], 3)
        self.assertEqual([i['name'] for i in services(self.snapshot.pk, query='bundle')['items']], ['svc-bundle'])
        with self.assertRaises(ValueError):
            services(self.snapshot.pk, view='all')
        self.assertEqual(describe({'service_entries': [{'resource_type': 'L4PortSetServiceEntry', 'l4_protocol': 'TCP', 'destination_ports': []}]})[1], ['Broad port range'])

    def test_scope_values_flag_case_variants_and_missing_vms(self):
        data = scopes(self.snapshot.pk)
        self.assertEqual(data['scopes'][0]['name'], 'app')
        values = {item['value']: item['notes'] for item in scopes(self.snapshot.pk, 'app')['values']}
        self.assertEqual(set(values), {'web', 'Web', 'reporting'})
        self.assertIn('Case differs from Web', values['web'])
        self.assertEqual(values['reporting'], ['No VMs in snapshot'])
        self.assertIn('VM tags only', values['Web'])

    def test_endpoints_require_login(self):
        url = reverse('snapshot-data', args=[self.snapshot.pk])
        self.assertEqual(self.client.get(url, {'op': 'service-list'}).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user('catalog-viewer'))
        self.assertEqual(self.client.get(url, {'op': 'service-list', 'view': 'unreferenced'}).json()['count'], 1)
        self.assertEqual(len(self.client.get(url, {'op': 'tag-scopes', 'scope': 'env'}).json()['values']), 1)
        self.assertEqual(self.client.get(url, {'op': 'service-list', 'view': 'bad'}).status_code, 400)

    def test_reference_object_picker_filters_by_kind_and_name(self):
        names = [item['name'] for item in reference_objects(self.snapshot.pk, 'svc', 'custom_service')['items']]
        self.assertEqual(names, ['svc-bundle', 'svc-old', 'svc-wide'])
        wide = reference_objects(self.snapshot.pk, 'wide', 'custom_service')['items'][0]
        self.assertEqual(wide['references'], 2)
        self.assertEqual(reference_objects(self.snapshot.pk, 'svc', 'group')['items'], [])
        with self.assertRaises(ValueError):
            reference_objects(self.snapshot.pk, '', 'vm')
