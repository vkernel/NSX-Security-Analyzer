from django.test import SimpleTestCase, TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from .impact import preview
from .models import Environment, Snapshot


def evidence():
    return {'inventory': {'groups': [
        {'path':'/infra/groups/a', 'name':'A', 'configuration':{'expression':[]}},
        {'path':'/infra/groups/b', 'name':'B', 'configuration':{'expression':[{'resource_type':'PathExpression','paths':['/infra/groups/a']}]}}]},
        'dfw': {'rules':[{'path':'/infra/p/rules/r','name':'R','policy_path':'/infra/p',
                        'configuration':{'rule':{'destination_groups':['/infra/groups/b']}}}],
                'policies':[{'path':'/infra/p','name':'P','rule_count':1}]}}


class ImpactGraphTests(SimpleTestCase):
    def test_group_chain_and_policy_containment(self):
        result = preview(evidence(), '/infra/groups/a')
        group = result['tree'][0]; rule = group['children'][0]; policy = rule['children'][0]
        self.assertEqual([group['name'],rule['name'],policy['name']], ['B','R','P'])
        self.assertEqual(rule['label'], 'Rule destination')
        self.assertEqual(preview(evidence(), '/infra/p')['tree'][0]['label'], 'Rule removed with policy')
        self.assertEqual(preview(evidence(), '/infra/p/rules/r')['dependencies'][0]['path'], '/infra/groups/b')

    def test_cycle_unresolved_and_limits_are_visible(self):
        report = evidence()
        report['inventory']['groups'][0]['configuration']['expression'] = [{'paths':['/infra/groups/b']}]
        report['inventory']['groups'][0]['referenced_by'] = ['/infra/unknown']
        result = preview(report, '/infra/groups/a')
        self.assertIn('Unresolved reference', str(result['tree']))
        self.assertIn("'repeated': True", str(result['tree']))
        self.assertTrue(any('truncated' in w for w in preview(report, '/infra/groups/a', limit=1)['warnings']))
        with self.assertRaises(KeyError): preview(report, '/missing')


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ImpactViewTests(TestCase):
    def test_authentication_readonly_escaping_and_snapshot_scope(self):
        env = Environment.objects.create(name='Impact',slug='impact',manager='https://nsx.example')
        report = evidence(); report['inventory']['groups'][0]['name'] = '<script>bad</script>'
        snap = Snapshot.objects.create(environment=env,generated_at=timezone.now(),report=report)
        url = reverse('impact-preview',args=[snap.pk])+'?path=/infra/groups/a'
        self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(get_user_model().objects.create_user('reader'))
        response = self.client.get(url)
        self.assertContains(response,'Dependency chain')
        self.assertContains(response,'&lt;script&gt;bad&lt;/script&gt;')
        self.assertEqual(self.client.post(url).status_code,405)
        self.assertEqual(self.client.get(reverse('impact-preview',args=[snap.pk])+'?path=/missing').status_code,404)
        snap.refresh_from_db(); self.assertEqual(snap.report,report)
        report.update(WorkloadImpactTests().report())
        snap.report = report; snap.save()
        base = reverse('impact-preview',args=[snap.pk])
        self.assertContains(self.client.get(base, {'action':'delete-vm','path':'vm-1'}), 'Workload')
        self.assertContains(self.client.get(base, {'action':'remove-tag','scope':'app','tag':'web','vm':'vm-1'}), 'Assignment to preview')
        self.assertEqual(self.client.get(base, {'action':'remove-tag','scope':'app','tag':'web','vm':'wrong'}).status_code,404)


class WorkloadImpactTests(SimpleTestCase):
    def report(self):
        report = evidence()
        report['tags'] = {'vm_inventory_complete':True, 'virtual_machines':[{'external_id':'vm-1','display_name':'Workload'}],
                          'objects':[{'scope':'app','name':'web','vms':{'vm-1':'Workload'},
                                      'group_conditions':{'/infra/groups/a':'A'}, 'group_assignments':{'/infra/groups/b':'B'}}]}
        return report

    def test_vm_chain_is_potential_and_metadata_is_not_membership(self):
        result = preview(self.report(), 'vm-1', action='delete-vm')
        self.assertEqual(result['root']['name'], 'Workload')
        self.assertEqual(len(result['tree']), 1)
        self.assertIn('Potential membership', result['tree'][0]['label'])
        self.assertEqual(result['tree'][0]['children'][0]['children'][0]['name'], 'R')

    def test_tag_assignment_scope_and_missing_assignment(self):
        result = preview(self.report(), '', action='remove-tag',scope='app',tag='web',vm='vm-1')
        self.assertIn('on Workload',result['root']['name'])
        self.assertIn('Potential',result['tree'][0]['label'])
        for params in ({'scope':'wrong','vm':'vm-1'}, {'scope':'app','vm':'missing'}):
            with self.assertRaises(KeyError):
                preview(self.report(), '', action='remove-tag',tag='web',**params)

    def test_incomplete_coverage_is_not_safe(self):
        report = self.report(); report['tags']['vm_inventory_complete'] = False
        result = preview(report,'vm-1',action='delete-vm')
        self.assertTrue(any('coverage is incomplete' in w for w in result['warnings']))
        with self.assertRaises(KeyError): preview(report,'vm-1',action='unsupported')
