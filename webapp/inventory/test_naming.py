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

    @staticmethod
    def definition(tags):
        return {'expression': [{'resource_type': 'Condition', 'member_type': 'VirtualMachine', 'key': 'Tag', 'operator': 'EQUALS', 'value': t['scope']+'|'+t['tag']} for t in tags]}

    def add_vm(self, identity, name, tags):
        return SnapshotRecord.objects.create(snapshot=self.snapshot, ordinal=SnapshotRecord.objects.count(), view='inventory', name=name, sort_name=name.lower(),
            compact={}, data={'path': identity, 'kind': 'group', 'configuration': self.definition(tags)}, columns=[], sort_values={}, search_basic='', search_evidence='')

    def rows(self, **kwargs):
        return naming.assess(self.env, **kwargs)[1]

    def url(self): return reverse('naming-compliance', args=[self.env.pk])

    def post(self, **kwargs):
        return self.client.post(self.url(), {'snapshot': str(self.snapshot.pk), 'selected': [self.vm.pk], **{'decision_stamp_'+str(r['id']):r['decision_stamp'] for r in self.rows()}, **kwargs})

    def payload(self):
        data = {'action': 'save', 'group_type': 'all', 'name': self.policy.name, 'separator': '-', 'max_length': 63, 'revision': self.policy.revision,
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
                self.vm.data['configuration']=self.definition(tags);self.vm.save()
                self.assertEqual(self.rows()[0]['status'], expected)
                self.assertEqual(self.rows()[0]['proposed'], '')

    def test_optional_missing_component_omits_separator(self):
        self.policy.configuration['components'][1]['required']=False;self.policy.save()
        self.vm.data['configuration']=self.definition(self.tags[:1]);self.vm.save()
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
        self.assertNotContains(self.client.get(reverse('naming-policies')), 'data-naming-components')
        self.assertContains(self.client.get(reverse('naming-policies')+'?new=1'), 'data-naming-components')

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
        self.vm.refresh_from_db();self.assertEqual(self.vm.data['configuration'],self.definition(self.tags))
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
        upload=SimpleUploadedFile('tags.csv',b'group_path,scope,value\nvm-1,Role,VDI\nvm-1,Instance,0042\n')
        self.assertEqual(self.post(action='import',csv_file=upload).status_code,302)
        self.assertEqual(NamingProposal.objects.get().tags,{'Role':'VDI','Instance':'0042'})

    def test_invalid_csv_has_no_partial_writes(self):
        upload=SimpleUploadedFile('tags.csv',b'group_path,scope,value\nvm-1,Role,VDI\nmissing,Role,VDI\n')
        self.assertContains(self.post(action='import',csv_file=upload),'unknown group')
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

    def test_classification_uses_definitions_including_empty_static_and_dynamic(self):
        for expression, expected in [([], 'static'),
            ([{'resource_type':'IPAddressExpression','ip_addresses':[]}], 'static'),
            (self.definition(self.tags)['expression'], 'dynamic'),
            (self.definition(self.tags)['expression']+[{'resource_type':'IPAddressExpression','ip_addresses':['10.0.0.1']}], 'mixed'),
            ([{'resource_type':'PathExpression','paths':['/infra/domains/default/groups/child']}], 'mixed')]:
            self.assertEqual(naming.group_evidence({'expression':expression})['group_type'],expected)
        self.assertEqual(naming.group_evidence(None)['group_type'],'unknown')

    def test_type_policy_and_prefix_mapping(self):
        config=copy.deepcopy(self.config);config['group_type']='dynamic'
        config['components'].insert(0,dict(config['components'][0],field='Type',source='type',mapping={'dynamic':'DGR'}))
        policy=NamingPolicy.objects.create(name='Dynamic',configuration=config,draft=config,enabled=True)
        self.assertEqual(self.rows()[0]['policy'],policy)
        self.assertEqual(self.rows()[0]['proposed'],'DGR-PRD-RTM')
        self.vm.data['configuration']={'expression':[]};self.vm.save()
        self.assertEqual(self.rows()[0]['policy'],self.policy)

    def test_static_member_tags_are_suggestions_not_automatic_components(self):
        self.vm.data['configuration']={'expression':[{'resource_type':'ExternalIDExpression','member_type':'VirtualMachine','external_ids':['external-1']}]};self.vm.save()
        SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=99,view='vms',name='member',sort_name='member',compact={},data={'path':'external-1','tags':self.tags},columns=[],sort_values={},search_basic='',search_evidence='')
        row=self.rows()[0]
        self.assertEqual(row['group_type'],'static');self.assertEqual(row['proposed'],'')
        self.assertEqual(row['supporting'][0]['tags'],self.tags)
        self.post(action='propose',scope='Environment',value='Production')
        self.post(action='propose',scope='Location',value='Rotterdam')
        self.assertEqual(self.rows()[0]['proposed'],'')
        self.post(action='review')
        self.assertEqual(self.rows()[0]['proposed'],'PRD-RTM')
        self.assertEqual(len(self.rows()),1)

    def test_complex_logic_needs_review_and_never_chooses_arbitrary_tags(self):
        self.vm.data['configuration']['expression'].insert(1,{'resource_type':'ConjunctionOperator','conjunction_operator':'OR'});self.vm.save()
        self.assertEqual(self.rows()[0]['status'],'Definition review required')
        self.post(action='propose',scope='Environment',value='Production')
        self.post(action='propose',scope='Location',value='Rotterdam')
        self.assertEqual(self.rows(simulation=True)[0]['proposed'],'')
        self.post(action='review')
        self.assertEqual(self.rows()[0]['proposed'],'PRD-RTM')
        self.vm.data['configuration']['expression'][0]['value']='Environment|Testing';self.vm.save()
        self.assertEqual(self.rows()[0]['status'],'Definition review required')
        self.assertTrue(self.rows()[0]['stale'])

    def test_nested_cycle_and_missing_definition_are_visible(self):
        self.vm.data['path']='/infra/domains/default/groups/a'
        self.vm.data['configuration']={'expression':[{'resource_type':'PathExpression','paths':['/infra/domains/default/groups/a','/infra/domains/default/groups/missing']}]};self.vm.save()
        details=str(self.rows()[0]['supporting'])
        self.assertIn('Cycle detected',details);self.assertIn('unavailable',details)
        self.assertEqual(self.rows()[0]['status'],'Definition review required')

    def test_exception_requires_admin_reason_and_invalidates_on_policy_change(self):
        self.assertContains(self.post(action='exception'),'record a reason')
        self.assertEqual(self.post(action='exception',note='Intentional integration name').status_code,302)
        self.assertEqual(self.rows()[0]['status'],'Reviewed exception')
        self.policy.revision+=1;self.policy.save()
        self.assertTrue(self.rows()[0]['exception_stale'])
        self.assertNotEqual(self.rows()[0]['status'],'Reviewed exception')
        self.post(action='reject')
        self.assertEqual(NamingProposal.objects.get().state,'rejected')
        self.user.is_superuser=False;self.user.save()
        self.assertContains(self.post(action='exception',note='reason'),'administrator')

    def test_legacy_vm_policy_and_proposals_never_apply_to_groups(self):
        self.policy.configuration.pop('target');self.policy.save()
        NamingProposal.objects.create(environment=self.env,object_type='vm',group_path='vm-1',tags={'Environment':'Testing'},baseline=self.rows()[0]['baseline'],state='reviewed')
        self.assertEqual(self.rows()[0]['status'],'No applicable policy')
        self.assertIsNone(self.rows()[0]['proposal'])

    def test_type_filter_and_csv_show_groups(self):
        self.assertContains(self.client.get(self.url()),'Group naming compliance')
        self.assertContains(self.client.get(self.url(),{'group_type':'static'}),'No matching groups')
        self.assertIn('group_path,group_type',self.client.get(self.url(),{'export':'csv'}).content.decode())

    def test_changed_proposal_cannot_be_reviewed_from_stale_page(self):
        self.post(action='propose',scope='Role',value='VDI')
        stamp=self.rows()[0]['decision_stamp']
        self.post(action='propose',scope='Role',value='Server')
        response=self.post(action='review',**{'decision_stamp_'+str(self.vm.pk):stamp})
        self.assertContains(response,'changed. Reload and review')
        self.assertEqual(NamingProposal.objects.get().state,'draft')

    def test_legacy_policy_requires_group_draft_before_activation(self):
        self.policy.draft.pop('target');self.policy.save()
        response=self.client.post(reverse('naming-policy-edit',args=[self.policy.pk]),{'action':'preview'})
        self.assertContains(response,'save a group policy draft')

    def test_scope_and_tag_catalog_counts_sources_and_environment_differences(self):
        other=Environment.objects.create(name='Amsterdam',slug='naming-ams',manager='https://ams.example')
        snapshot=Snapshot.objects.create(environment=other,generated_at=timezone.now(),summary={},report={})
        SnapshotRecord.objects.create(snapshot=snapshot,ordinal=0,view='vms',name='VM',sort_name='vm',compact={},data={'tags':[{'scope':'VMOnly','tag':'Suggested'},{'scope':'VMOnly','tag':'Suggested'}]},columns=[],sort_values={},search_basic='',search_evidence='')
        url=reverse('naming-suggestions')
        response=self.client.get(url).json()
        scope=next(r for r in response['results'] if r['value']=='Environment')
        self.assertEqual(scope['groups'],1);self.assertEqual(scope['missing_environments'],['Amsterdam'])
        vm_scope=next(r for r in response['results'] if r['value']=='VMOnly')
        self.assertEqual(vm_scope['groups'],0);self.assertEqual(vm_scope['vms'],1)
        values=self.client.get(url,{'environment':self.env.pk,'scope':'Environment','q':'prod'}).json()
        self.assertEqual([r['value'] for r in values['results']],['Production'])
        self.assertEqual(self.client.get(url,{'environment':self.env.pk,'scope':'VMOnly'}).json()['results'],[])

    def test_catalog_missing_coverage_and_no_mutations(self):
        self.snapshot.browsing_status='archived';self.snapshot.save()
        result=self.client.get(reverse('naming-suggestions'),{'environment':self.env.pk}).json()
        self.assertEqual(result['results'],[]);self.assertIn('no ready full collection',result['warnings'][0])
        self.assertEqual(self.client.post(reverse('naming-suggestions')).status_code,405)
        self.assertEqual(NamingProposal.objects.count(),0)
        self.client.logout();self.assertEqual(self.client.get(reverse('naming-suggestions')).status_code,302)

    def test_live_example_uses_draft_order_and_never_saves_policy(self):
        data=self.payload();data['environment']=self.env.pk
        data['components-0-ORDER']=2;data['components-1-ORDER']=1
        response=self.client.post(reverse('naming-example'),data)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['proposed'],'RTM-PRD')
        from .models import AuditEvent
        self.assertFalse(AuditEvent.objects.filter(action='http.mutation_attempt',target_id='naming/example/').exists())
        self.policy.refresh_from_db();self.assertEqual(self.policy.revision,0)
        self.assertEqual(self.policy.versions.count(),0);self.assertEqual(NamingProposal.objects.count(),0)
        viewer=get_user_model().objects.create_user('catalog-viewer')
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('naming-suggestions')).status_code,200)
        self.assertEqual(self.client.post(reverse('naming-example'),data).status_code,403)

    def test_live_example_invalid_fields_return_help(self):
        self.assertIn('Complete',self.client.post(reverse('naming-example'),{}).json()['message'])

    def test_remove_policy_preserves_history_and_excludes_assessment(self):
        url = reverse('naming-policy-edit', args=[self.policy.pk])
        self.assertContains(self.client.get(url), 'Remove policy')
        response = self.client.post(url, {'action': 'remove', 'revision': self.policy.revision, 'confirm_remove': 'yes'})
        self.assertRedirects(response, reverse('naming-policies'))
        self.policy.refresh_from_db()
        self.assertIsNotNone(self.policy.removed_at)
        self.assertFalse(self.policy.enabled)
        self.assertEqual(self.policy.versions.get().action, 'removed')
        self.assertIsNone(self.rows()[0]['policy'])
        self.assertNotContains(self.client.get(reverse('naming-policies')), url)
        self.assertContains(self.client.get(reverse('naming-policies') + '?show=removed'), url)
        self.assertContains(self.client.get(url), 'Saved configurations are read-only')
        for action in ['activate', 'save', 'binding', 'preview', 'disable', 'remove']:
            self.assertEqual(self.client.post(url, {'action': action}).status_code, 409)

    def test_remove_requires_confirmation_current_revision_and_admin(self):
        url = reverse('naming-policy-edit', args=[self.policy.pk])
        self.client.post(url, {'action': 'remove', 'revision': self.policy.revision})
        self.policy.refresh_from_db()
        self.assertIsNone(self.policy.removed_at)
        self.assertEqual(self.client.post(url, {'action': 'remove', 'revision': 999, 'confirm_remove': 'yes'}).status_code, 409)
        self.user.is_superuser = False
        self.user.save()
        self.assertEqual(self.client.post(url, {'action': 'remove', 'revision': 0, 'confirm_remove': 'yes'}).status_code, 403)
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.enabled)
