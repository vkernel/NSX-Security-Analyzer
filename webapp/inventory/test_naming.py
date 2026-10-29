import copy
import io
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import naming
from .models import Environment, Snapshot, SnapshotRecord, NamingPolicy, NamingScopeBinding, NamingProposal, NamingPolicyVersion
from .naming_views import DEFAULT


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class NamingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('naming-admin', password='test-only')
        self.env = Environment.objects.create(name='Rotterdam', slug='naming-rtm', manager='https://nsx.example')
        self.snapshot = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), summary={}, report={})
        self.config = copy.deepcopy(DEFAULT)
        self.config['components'] = self.config['components'][:2]
        self.config['components'][0]['mapping'] = {'Production': 'PRD'}
        self.config['components'][1]['mapping'] = {'Rotterdam': 'RTM'}
        self.policy = NamingPolicy.objects.create(name='VM names', draft=self.config, configuration=self.config, enabled=True)
        self.tags = [{'scope': 'Environment', 'tag': 'Production'}, {'scope': 'Location', 'tag': 'Rotterdam'}]
        self.vm = self.add_vm('vm-1', 'old-name', self.tags)
        self.client.force_login(self.user)

    def add_vm(self, identity, name, tags):
        return SnapshotRecord.objects.create(snapshot=self.snapshot, ordinal=SnapshotRecord.objects.count(), view='vms', name=name, sort_name=name.lower(),
            compact={}, data={'path': identity, 'tags': tags}, columns=[], sort_values={}, search_basic='', search_evidence='')

    def rows(self, **kwargs):
        return naming.assess(self.env, **kwargs)[1]

    def url(self): return reverse('naming-compliance', args=[self.env.pk])

    def post(self, **kwargs):
        return self.client.post(self.url(), {'snapshot': str(self.snapshot.pk), 'selected': [self.vm.pk], **kwargs})

    def payload(self):
        data = {'action': 'save', 'name': self.policy.name, 'separator': '-', 'max_length': 63, 'revision': self.policy.revision,
                'components-TOTAL_FORMS': 2, 'components-INITIAL_FORMS': 2, 'components-MIN_NUM_FORMS': 1, 'components-MAX_NUM_FORMS': 16}
        for index, component in enumerate(self.config['components']):
            for key, value in component.items():
                if key == 'mapping': value='\n'.join(f'{a}={b}' for a,b in value.items());key='mapping_text'
                data[f'components-{index}-{key}'] = value
            data[f'components-{index}-ORDER'] = index+1
        return data

    def test_order_case_mapping_and_observed_name(self):
        row=self.rows()[0]
        self.assertEqual(row['proposed'], 'PRD-RTM')
        self.assertEqual(row['status'], 'Name differs')
        self.assertEqual([c['field'] for c in row['components']], ['Environment', 'Location'])
        self.vm.name='PRD-RTM';self.vm.save()
        self.assertEqual(self.rows()[0]['status'], 'Compliant')

    def test_missing_ambiguous_and_unmapped_tags_are_explicit(self):
        for tags, expected in [([], 'Missing required tags'), (self.tags+[{'scope':'Environment','tag':'Test'}], 'Ambiguous tags'),
                               ([{'scope':'Environment','tag':'Unknown'},self.tags[1]], 'Invalid value')]:
            with self.subTest(expected=expected):
                self.vm.data['tags']=tags;self.vm.save()
                self.assertEqual(self.rows()[0]['status'], expected)
                self.assertEqual(self.rows()[0]['proposed'], '')

    def test_optional_missing_component_omits_separator(self):
        self.policy.configuration['components'][1]['required']=False;self.policy.save()
        self.vm.data['tags']=self.tags[:1];self.vm.save()
        self.assertEqual(self.rows()[0]['proposed'], 'PRD')

    def test_fixed_component_and_identity_suffix(self):
        config=copy.deepcopy(self.config)
        config['components'][1].update(source='fixed', literal='VM', mapping={})
        config['components'].append(dict(config['components'][1], field='ID', source='identity', format='extended'))
        self.policy.configuration=config;self.policy.save()
        self.assertEqual(self.rows()[0]['proposed'], 'PRD-VM-VM-1')

    def test_total_length_and_invalid_characters(self):
        self.policy.configuration['max_length']=3;self.policy.save()
        self.assertEqual(self.rows()[0]['status'], 'Invalid value')

    def test_duplicate_names_and_existing_vm_names(self):
        self.add_vm('vm-2','PRD-RTM',[])
        self.assertEqual(self.rows()[0]['status'], 'Duplicate proposed name')

    def test_duplicate_policy_is_not_silently_selected(self):
        NamingPolicy.objects.create(name='Conflict', enabled=True, configuration=self.config, draft=self.config)
        self.assertEqual(self.rows()[0]['status'], 'Ambiguous policy')

    def test_local_override_and_category_precedence(self):
        local=NamingPolicy.objects.create(name='Local', environment=self.env, enabled=True, configuration=self.config, draft=self.config)
        self.assertEqual(self.rows()[0]['policy'].pk,local.pk)
        config=copy.deepcopy(self.config);config.update(match_field='Environment',match_value='Production')
        specific=NamingPolicy.objects.create(name='Local production', environment=self.env, enabled=True, configuration=config,draft=config)
        self.assertEqual(self.rows()[0]['policy'].pk,specific.pk)

    def test_nonmatching_category_falls_back_to_global_default(self):
        config=copy.deepcopy(self.config);config.update(match_field='Environment',match_value='Testing')
        NamingPolicy.objects.create(name='Testing', environment=self.env, enabled=True, configuration=config,draft=config)
        self.assertEqual(self.rows()[0]['policy'].pk,self.policy.pk)

    def test_scope_bindings_only_apply_after_activation(self):
        NamingScopeBinding.objects.create(policy=self.policy, environment=self.env, scopes={}, draft_scopes={'Environment':'EnvCode'})
        self.assertEqual(self.rows()[0]['proposed'],'PRD-RTM')
        self.assertEqual(self.rows(replacement=self.policy)[0]['status'],'Missing required tags')

    def test_coverage_warning_never_establishes_compliance(self):
        self.vm.name='PRD-RTM';self.vm.save()
        self.snapshot.summary={'inventory_warnings':True};self.snapshot.save()
        self.assertEqual(self.rows()[0]['status'],'Evidence incomplete')

    def test_archived_snapshot_is_not_empty_compliance(self):
        self.snapshot.browsing_status='archived';self.snapshot.save()
        snapshot,rows,warning=naming.assess(self.env)
        self.assertEqual(rows,[]);self.assertIn('Restore',warning)

    def test_compliance_and_editor_render(self):
        self.assertContains(self.client.get(self.url()),'PRD-RTM')
        self.assertContains(self.client.get(reverse('naming-policy-edit',args=[self.policy.pk])),'Ordered name components')
        self.assertContains(self.client.get(reverse('naming-policies')),'New policy')

    def test_saving_draft_preserves_active_config_and_version_history(self):
        data=self.payload();data['separator']='_'
        response=self.client.post(reverse('naming-policy-edit',args=[self.policy.pk]),data)
        self.assertEqual(response.status_code,302)
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.draft['separator'],'_')
        self.assertEqual(self.policy.configuration['separator'],'-')
        self.assertEqual(self.policy.versions.count(),1)

    def test_preview_then_activation_and_stale_preview_rejected(self):
        url=reverse('naming-policy-edit',args=[self.policy.pk])
        response=self.client.post(url,{'action':'activate','acknowledge':'yes'})
        self.assertContains(response,'Preview again')
        self.client.post(url,{'action':'preview'})
        self.vm.name='changed';self.vm.save()
        self.assertContains(self.client.post(url,{'action':'activate','acknowledge':'yes'}),'Preview again')
        self.client.post(url,{'action':'preview'})
        self.assertEqual(self.client.post(url,{'action':'activate','acknowledge':'yes'}).status_code,302)
        self.policy.refresh_from_db();self.assertTrue(self.policy.enabled)
        self.assertTrue(self.policy.versions.filter(action='activated').exists())

    def test_binding_edit_does_not_change_observed_compliance(self):
        url=reverse('naming-policy-edit',args=[self.policy.pk])
        response=self.client.post(url,{'action':'binding','environment':self.env.pk,'field':'Environment','scope':'Other','revision':0})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.rows()[0]['proposed'],'PRD-RTM')
        self.client.post(url,{'action':'preview'})
        self.client.post(url,{'action':'activate','acknowledge':'yes'})
        self.assertEqual(self.rows()[0]['status'],'Missing required tags')

    def test_stale_policy_save_is_rejected(self):
        self.policy.revision=1;self.policy.save()
        data=self.payload();data['revision']=0
        self.assertContains(self.client.post(reverse('naming-policy-edit',args=[self.policy.pk]),data),'policy changed')

    def test_duplicate_logical_fields_rejected(self):
        data=self.payload();data['components-1-field']='Environment'
        self.assertContains(self.client.post(reverse('naming-policy-edit',args=[self.policy.pk]),data),'Each logical field must be unique')

    def test_proposals_do_not_change_observed_tags_and_simulate_separately(self):
        response=self.post(action='propose',scope='Environment',value='Testing')
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.rows()[0]['status'],'Name differs')
        self.assertEqual(self.rows(simulation=True)[0]['status'],'Invalid value')
        self.vm.refresh_from_db();self.assertEqual(self.vm.data['tags'],self.tags)
        self.assertEqual(self.post(action='review').status_code,302)
        self.assertEqual(NamingProposal.objects.get().state,'reviewed')

    def test_stale_proposal_cannot_be_reviewed_or_simulated(self):
        self.post(action='propose',scope='Environment',value='Testing')
        self.vm.name='changed';self.vm.save()
        self.assertContains(self.post(action='review'),'Stale or empty proposals')
        self.assertEqual(self.rows(simulation=True)[0]['proposed'],'PRD-RTM')
        self.assertTrue(self.rows()[0]['stale'])
        self.assertEqual(self.post(action='reject').status_code,302)
        self.assertEqual(self.post(action='propose',scope='Environment',value='Production').status_code,302)

    def test_bulk_assignment_and_csv_import(self):
        self.assertEqual(self.post(action='assign',owner=self.user.pk).status_code,302)
        self.assertEqual(NamingProposal.objects.get().owner_id,self.user.pk)
        upload=SimpleUploadedFile('tags.csv',b'vm_identity,scope,value\nvm-1,Role,VDI\nvm-1,Instance,0042\n')
        self.assertEqual(self.post(action='import',csv_file=upload).status_code,302)
        self.assertEqual(NamingProposal.objects.get().tags,{'Role':'VDI','Instance':'0042'})

    def test_invalid_csv_has_no_partial_writes(self):
        upload=SimpleUploadedFile('tags.csv',b'vm_identity,scope,value\nvm-1,Role,VDI\nmissing,Role,VDI\n')
        self.assertContains(self.post(action='import',csv_file=upload),'unknown VM')
        self.assertEqual(NamingProposal.objects.count(),0)

    def test_changed_snapshot_and_foreign_vm_selection_rejected(self):
        self.assertContains(self.post(action='propose',scope='Role',value='VDI',snapshot='stale'),'Inventory changed')
        self.assertContains(self.post(action='propose',scope='Role',value='VDI',selected=['99999']),'Select 1')
        self.assertEqual(NamingProposal.objects.count(),0)

    def test_csv_export_escapes_spreadsheet_formulas(self):
        self.vm.name='=CMD()';self.vm.save()
        response=self.client.get(self.url(),{'export':'csv'})
        self.assertIn("'=CMD()",response.content.decode())

    def test_viewer_can_read_but_not_mutate(self):
        viewer=get_user_model().objects.create_user('naming-viewer')
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self.url()).status_code,200)
        self.assertEqual(self.post(action='assign',owner=self.user.pk).status_code,403)
        self.assertEqual(self.client.get(reverse('naming-policies')).status_code,403)
