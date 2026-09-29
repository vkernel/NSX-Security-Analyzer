from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings, Client
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse
from .models import Environment, IPFIXExporter, IPFIXReceiver, IPFIXSetup
from .ipfix.setup import make_plan, apply_plan, SetupError, COLLECTORS, PROFILES
from .ipfix_setup_views import SetupForm


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SetupTests(TestCase):
    def setUp(self):
        self.environment=Environment.objects.create(name='Synthetic',slug='synthetic',manager='https://nsx.example.invalid')
        self.user=get_user_model().objects.create_user(username='setup',is_staff=True)
        self.client.force_login(self.user)
        IPFIXReceiver.objects.create(pk=1,heartbeat=timezone.now())
        self.collector={'path':COLLECTORS+'/existing','resource_type':'IPFIXDFWCollectorProfile','_revision':3,
            'ipfix_dfw_collectors':[{'collector_ip_address':'192.0.2.9','collector_port':2055}]}
        self.profile={'path':PROFILES+'/existing','ipfix_dfw_collector_profile_path':self.collector['path']}
        self.api=Mock()
        self.api.items.return_value=[self.profile]
        self.api.get.return_value=self.collector

    def plan(self):
        return make_plan(self.api,self.environment,'192.0.2.10',2055)

    def record(self):
        return IPFIXSetup.objects.create(environment=self.environment,actor=self.user,plan=self.plan())

    def test_preview_retains_destinations_and_revision_without_writes(self):
        plan=self.plan()
        self.assertEqual(len(plan['operations']),1)
        body=plan['operations'][0]['body']
        self.assertEqual(body['_revision'],3)
        self.assertEqual(body['ipfix_dfw_collectors'][0],self.collector['ipfix_dfw_collectors'][0])
        self.assertEqual(len(self.collector['ipfix_dfw_collectors']),1)
        self.assertEqual(body['ipfix_dfw_collectors'][1]['collector_ip_address'],'192.0.2.10')
        self.api.http.request.assert_not_called()

    @patch('inventory.ipfix.setup.put')
    @patch('inventory.ipfix.setup.client_for')
    def test_apply_once_enables_receiver_and_mappings(self,client_for,put):
        record=self.record()
        client_for.return_value=self.api
        result=apply_plan(record.pk,self.user)
        self.assertEqual(result.status,'applied')
        self.assertTrue(IPFIXReceiver.objects.get().enabled)
        self.assertFalse(IPFIXExporter.objects.exists())
        put.assert_called_once()
        with self.assertRaises(SetupError): apply_plan(record.pk,self.user)
        self.assertEqual(put.call_count,1)

    @patch('inventory.ipfix.setup.put')
    @patch('inventory.ipfix.setup.client_for')
    def test_concurrent_nsx_change_blocks_writes(self,client_for,put):
        record=self.record()
        changed=deepcopy(self.collector); changed['_revision']=4
        self.api.get.return_value=changed
        client_for.return_value=self.api
        self.assertEqual(apply_plan(record.pk,self.user).status,'failed')
        put.assert_not_called()
        self.assertFalse(IPFIXReceiver.objects.get().enabled)

    @patch('inventory.ipfix.setup.put',side_effect=SetupError('HTTP 403'))
    @patch('inventory.ipfix.setup.client_for')
    def test_denied_write_does_not_enable_local_reception(self,client_for,put):
        record=self.record(); client_for.return_value=self.api
        self.assertEqual(apply_plan(record.pk,self.user).status,'failed')
        self.assertFalse(IPFIXExporter.objects.exists())
        self.assertFalse(IPFIXReceiver.objects.get().enabled)

    def test_receiver_and_mapping_conflicts_block_preview(self):
        other=Environment.objects.create(name='Other',slug='other',manager='https://other.example.invalid')
        IPFIXExporter.objects.create(environment=other,address='192.0.2.20')
        self.plan()  # Source mappings do not block NSX configuration.
        IPFIXExporter.objects.all().delete()
        IPFIXReceiver.objects.update(heartbeat=timezone.now()-timedelta(minutes=1))
        with self.assertRaises(SetupError): self.plan()

    @patch('inventory.ipfix.setup.read_optional',return_value=None)
    def test_new_profile_includes_explicit_group_binding(self,optional):
        self.api.items.return_value=[]
        self.api.get.return_value={'resource_type':'Group'}
        plan=make_plan(self.api,self.environment,'192.0.2.10',2055,'/infra/domains/default/groups/synthetic')
        self.assertEqual(len(plan['operations']),3)
        self.assertEqual(plan['operations'][-1]['body']['ipfix_dfw_profile_path'],plan['operations'][1]['path'])
        with self.assertRaises(SetupError): self.plan()

    def test_approval_csrf_ownership_and_staff(self):
        record=self.record(); url=reverse('ipfix-setup-review',args=[record.pk])
        with patch('inventory.ipfix_setup_views.apply_plan') as apply:
            self.client.post(url,{})
            apply.assert_not_called()
        guarded=Client(enforce_csrf_checks=True); guarded.force_login(self.user)
        self.assertEqual(guarded.post(url,{'approve':'on'}).status_code,403)
        other=get_user_model().objects.create_user(username='other',is_staff=True)
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code,404)
        other.is_staff=False; other.save()
        self.assertEqual(self.client.get(reverse('ipfix-setup')).status_code,403)

    def test_source_destination_confusion_is_rejected(self):
        form=SetupForm({'environment':self.environment.pk,'destination':'192.0.2.10','port':2055,'sources':'192.0.2.10'})
        self.assertTrue(form.is_valid(),form.errors)
        self.assertNotIn('sources',form.fields)

    def test_expired_preview_cannot_apply(self):
        record=self.record()
        IPFIXSetup.objects.filter(pk=record.pk).update(created_at=timezone.now()-timedelta(minutes=11))
        with self.assertRaises(SetupError): apply_plan(record.pk,self.user)

    def test_delivery_requires_new_valid_datagrams(self):
        record=self.record(); record.status='applied'; record.applied_at=timezone.now()
        record.plan['baseline']={'192.0.2.20':5}; record.save()
        exporter=IPFIXExporter.objects.create(environment=self.environment,address='192.0.2.20',messages=6,malformed=1,last_received=timezone.now())
        url=reverse('ipfix-setup-review',args=[record.pk])
        self.assertContains(self.client.get(url),'waiting for exporter traffic')
        exporter.messages=7; exporter.save()
        self.assertContains(self.client.get(url),'Valid datagrams observed')

    @patch('inventory.ipfix.setup.put')
    @patch('inventory.ipfix.setup.client_for')
    def test_partial_failure_retains_acknowledged_outcome(self,client_for,put):
        second=deepcopy(self.collector); second['path']=COLLECTORS+'/second'
        self.api.items.return_value=[self.profile,dict(self.profile,path=PROFILES+'/second',ipfix_dfw_collector_profile_path=second['path'])]
        self.api.get.side_effect=lambda path: second if path==second['path'] else self.collector
        record=self.record(); client_for.return_value=self.api
        put.side_effect=[None,SetupError('Write outcome unknown')]
        result=apply_plan(record.pk,self.user)
        result.refresh_from_db()
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.outcomes,[self.collector['path']])
        self.assertFalse(IPFIXReceiver.objects.get().enabled)

    def test_blank_sources_uses_environment_mappings(self):
        IPFIXExporter.objects.create(environment=self.environment,address='192.0.2.20')
        form=SetupForm({'environment':self.environment.pk,'destination':'192.0.2.10','port':2055})
        self.assertTrue(form.is_valid(),form.errors)
        self.assertNotIn('sources',form.cleaned_data)
        self.assertEqual(self.plan()['sources'],['192.0.2.20'])

    def test_write_transport_never_redirects_or_retries(self):
        from .ipfix.setup import put
        self.api.base_url='https://nsx.example.invalid/policy/api/v1'
        self.api.headers={'Authorization':'Basic synthetic'}
        self.api.http.request.return_value.status=200
        put(self.api,self.collector['path'],{'_revision':3})
        self.assertFalse(self.api.http.request.call_args.kwargs['redirect'])
        self.assertFalse(self.api.http.request.call_args.kwargs['retries'])
        with self.assertRaises(SetupError): put(self.api,'/infra/anything-else',{})

    def test_writable_system_owned_collector_is_explicit_in_preview(self):
        self.collector['_system_owned']=True
        self.collector['_protection']='NOT_PROTECTED'
        self.assertIn('system-owned',self.plan()['operations'][0]['action'])
        self.collector['_protection']='REQUIRE_OVERRIDE'
        with self.assertRaises(SetupError): self.plan()

    def test_receiver_address_mapping_does_not_block_or_change_setup(self):
        mapping=IPFIXExporter.objects.create(environment=self.environment,address='192.0.2.10')
        plan=self.plan()
        self.assertEqual(plan['sources'],[])
        mapping.refresh_from_db()
        self.assertTrue(mapping.enabled)
        response=self.client.get(reverse('ipfix-setup'))
        self.assertNotContains(response,'name="sources"')
        self.assertContains(response,'Exporter sources')

    def test_sources_page_is_separate_and_staff_only(self):
        self.assertContains(self.client.get(reverse('ipfix-sources')),'Add exporters')
        self.user.is_staff=False;self.user.save()
        self.assertEqual(self.client.get(reverse('ipfix-sources')).status_code,403)

    def test_existing_mode_changes_only_selected_collectors(self):
        self.api.items.return_value=[self.profile,dict(self.profile,path=PROFILES+'/other',ipfix_dfw_collector_profile_path=COLLECTORS+'/other')]
        plan=make_plan(self.api,self.environment,'192.0.2.10',2055,profile_mode='existing',existing_profile=self.profile['path'])
        self.assertEqual([op['path'] for op in plan['operations']],[self.collector['path']])
        with self.assertRaises(SetupError):
            make_plan(self.api,self.environment,'192.0.2.10',2055,profile_mode='existing',existing_profile=PROFILES+'/missing')

    @patch('inventory.ipfix.setup.read_optional',return_value=None)
    def test_new_mode_creates_and_binds_even_with_existing_profile(self,optional):
        self.api.get.return_value={'resource_type':'Group'}
        self.profile['priority']=0
        plan=make_plan(self.api,self.environment,'192.0.2.10',2055,'/infra/domains/default/groups/synthetic',
            profile_mode='new',profile_name='Dedicated telemetry',priority=50)
        self.assertEqual(len(plan['operations']),3)
        self.assertTrue(all(op['before'] is None for op in plan['operations']))
        self.assertEqual(plan['operations'][1]['body']['priority'],50)
        self.assertEqual(plan['operations'][1]['body']['display_name'],'Dedicated telemetry')
        self.assertIn('priority 0',plan['warnings'][0])

    def test_new_form_requires_scope_and_valid_priority(self):
        data={'environment':self.environment.pk,'destination':'192.0.2.10','port':2055,'profile_mode':'new','priority':-1}
        form=SetupForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('priority',form.errors)
        self.assertIn('group',form.errors)

    @patch('inventory.ipfix.setup.read_optional',return_value=None)
    def test_existing_profile_can_be_bound_to_group(self,optional):
        self.api.get.side_effect=lambda path: {'resource_type':'Group'} if '/groups/' in path else self.collector
        plan=make_plan(self.api,self.environment,'192.0.2.10',2055,'/infra/domains/default/groups/synthetic',profile_mode='existing',existing_profile=self.profile['path'])
        self.assertEqual(plan['operations'][-1]['body']['ipfix_dfw_profile_path'],self.profile['path'])
