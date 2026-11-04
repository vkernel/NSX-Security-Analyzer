from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import naming
from .models import Environment, NamingPolicy, NamingProposal, Snapshot, SnapshotRecord
from .naming_forms import ComponentForm
from .test_naming_host_ip import definition, host_config


def subnet_config():
    config = host_config()
    config['match_mode'] = 'single_subnet'
    config['components'][1]['literal'] = 'NET'
    config['components'][2].update(source='single_subnet', format='ip_subnet')
    return config


class ExplicitSubnetTests(SimpleTestCase):
    def test_canonical_ipv4_ipv6_and_default_routes(self):
        for value, expected in [('10.128.196.150/24','10.128.196.0/24'), ('10.128.196.0/24','10.128.196.0/24'),
                                ('2001:0DB8::123/64','2001:db8::/64'), ('0.0.0.0/0','0.0.0.0/0'), ('::/0','::/0')]:
            with self.subTest(value=value):
                group = {'definition': definition(value)}
                self.assertEqual(naming.explicit_subnet(group), expected)
                self.assertIsNone(naming.explicit_host_ip(group))

    def test_ambiguous_complex_and_host_definitions_are_excluded(self):
        cases = [None, {}, definition(), definition('10.0.0.1'), definition('10.0.0.1/32'), definition('::1/128'),
                 definition('10.0.0.0/24','10.1.0.0/24'), definition('10.0.0.0/24','10.0.0.0/24'),
                 definition('10.0.0.1-10.0.0.10'), definition('10.0.0.0/33'), definition('fe80::%eth0/64'),
                 definition(123), definition('invalid'), {'expression':[None]},
                 {'expression':[{'resource_type':'IPAddressExpression','ip_addresses':None}]},
                 {'expression':[{'resource_type':'PathExpression','paths':['/infra/groups/child']}]},
                 {'expression':definition('10.0.0.0/24')['expression']+[{'resource_type':'Condition','key':'Tag'}]},
                 dict(definition('10.0.0.0/24'),extended_expression=[{'resource_type':'IdentityGroupExpression'}])]
        for value in cases:
            with self.subTest(definition=value):
                self.assertIsNone(naming.explicit_subnet({'definition':value}))

    def test_subnet_format_is_required_and_mapping_cannot_replace_membership(self):
        data = dict(field='Address',source='single_subnet',required=True,case='keep',format='ip_subnet',max_length=49)
        self.assertTrue(ComponentForm(data).is_valid())
        for change, field in [({'case':'upper'},'case'),({'format':'ip_address'},'format'),({'required':False},'required'),
                              ({'mapping_text':'10.0.0.0_24=10.1.0.0_24'},'mapping_text')]:
            with self.subTest(change=change):
                form = ComponentForm(dict(data,**change))
                self.assertFalse(form.is_valid()); self.assertIn(field,form.errors)


@override_settings(STORAGES={'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SubnetNamingPolicyTests(TestCase):
    def setUp(self):
        self.env = Environment.objects.create(name='Zwolle',slug='subnet-naming',manager='https://offline.invalid')
        self.snapshot = Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),summary={},report={})
        self.config = subnet_config()
        self.policy = NamingPolicy.objects.create(name='Zwolle static subnets',environment=self.env,enabled=True,configuration=self.config,draft=self.config)
        self.client.force_login(get_user_model().objects.create_superuser('subnet-admin',password='offline-test-only'))

    def add(self, value='10.128.196.0/24', name='SZ-TZW-EXT-NET-10.128.196.0_24', config=None):
        number = SnapshotRecord.objects.count()
        return SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=number,view='inventory',name=name,sort_name=name,compact={},
            data={'kind':'group','path':f'/infra/domains/default/groups/subnet-{number}','configuration':config or definition(value)},
            columns=[],sort_values={},search_basic='',search_evidence='')

    def payload(self):
        data = dict(action='save',name='Automatic subnet draft',environment=self.env.pk,group_type='static',match_mode='single_subnet',separator='-',max_length=128,revision=0,
                    **{'components-TOTAL_FORMS':3,'components-INITIAL_FORMS':0,'components-MIN_NUM_FORMS':1,'components-MAX_NUM_FORMS':16})
        for index, component in enumerate(self.config['components']):
            for key,value in component.items():
                if key == 'mapping': key,value = 'mapping_text',''
                data[f'components-{index}-{key}'] = value
            data[f'components-{index}-ORDER'] = index+1
        return data

    def test_150_subnets_without_manual_proposals_and_membership_changes(self):
        for number in range(150):
            self.add(f'10.128.{number}.99/24',f'SZ-TZW-EXT-NET-10.128.{number}.0_24' if number != 100 else 'incorrect-name')
        rows = naming.assess(self.env)[1]
        self.assertEqual(naming.queue_counts(rows),{'all':150,'compliant':149,'attention':1,'exceptions':0})
        self.assertEqual(next(row for row in rows if row['name']=='incorrect-name')['proposed'],'SZ-TZW-EXT-NET-10.128.100.0_24')
        self.assertEqual(NamingProposal.objects.count(),0)
        self.assertEqual(naming.assess(self.env)[1][0]['proposed'],rows[0]['proposed'])

    def test_reviewed_values_cannot_override_actual_network(self):
        record = self.add('10.128.196.150/24'); row = naming.assess(self.env)[1][0]
        NamingProposal.objects.create(environment=self.env,group_path=row['identity'],object_type='group',baseline=naming.baseline(row),state='reviewed',tags={'naming/address':'203.0.113.0_24'})
        self.assertEqual(naming.assess(self.env)[1][0]['status'],'Compliant')
        record.data['configuration'] = definition('10.128.197.151/24'); record.save()
        self.assertEqual(naming.assess(self.env)[1][0]['proposed'],'SZ-TZW-EXT-NET-10.128.197.0_24')

    def test_hosts_subnets_and_general_fallback_do_not_overlap(self):
        host = host_config()
        host_policy = NamingPolicy.objects.create(name='Hosts',environment=self.env,enabled=True,configuration=host,draft=host)
        fallback = host_config(); fallback['match_mode'] = 'exact'; fallback['components'] = fallback['components'][:1]
        other = NamingPolicy.objects.create(name='Static fallback',environment=self.env,enabled=True,configuration=fallback,draft=fallback)
        self.add(); self.add('10.0.0.1/32',name='SZ-TZW-EXT-HST-10.0.0.1'); self.add(name='Multiple addresses',config=definition('10.0.0.1','10.0.0.2'))
        rows = naming.assess(self.env)[1]
        self.assertEqual([row['policy'].pk for row in rows],[self.policy.pk,host_policy.pk,other.pk])
        self.assertEqual([row['status'] for row in rows[:2]],['Compliant','Compliant'])

    def test_ipv6_and_canonical_network_collisions_are_checked(self):
        self.add('2001:0DB8::123/64',name='SZ-TZW-EXT-NET-2001:db8::_64')
        self.assertEqual(naming.assess(self.env)[1][0]['status'],'Compliant')
        self.add('10.0.0.10/24',name='first'); self.add('10.0.0.20/24',name='second')
        rows = naming.assess(self.env)[1]
        self.assertTrue(all(row['status']=='Duplicate proposed name' for row in rows[1:]))

    def test_duplicate_subnet_policies_remain_ambiguous(self):
        self.add()
        NamingPolicy.objects.create(name='Overlapping subnet policy',environment=self.env,enabled=True,configuration=self.config,draft=self.config)
        self.assertEqual(naming.assess(self.env)[1][0]['status'],'Ambiguous policy')

    def test_save_live_example_preview_and_activation(self):
        self.add('10.128.196.150/24'); self.policy.enabled=False; self.policy.save()
        example = self.client.post(reverse('naming-example'),self.payload()).json()
        self.assertEqual(example['proposed'],'SZ-TZW-EXT-NET-10.128.196.0_24')
        self.assertEqual(example['components'][-1]['source'],'Explicit IP membership')
        self.assertNotIn('Prepare component values',example['message'])
        self.assertEqual(self.client.post(reverse('naming-policies'),self.payload()).status_code,302)
        policy = NamingPolicy.objects.get(name='Automatic subnet draft'); url = reverse('naming-policy-edit',args=[policy.pk])
        preview = self.client.post(url,{'action':'preview'})
        self.assertEqual(preview.context['preview']['summaries'][0]['policy_counts']['compliant'],1)
        self.assertEqual(self.client.post(url,{'action':'activate','acknowledge':'yes'}).status_code,302)
        self.assertEqual(naming.assess(self.env)[1][0]['policy'].pk,policy.pk)
        self.assertEqual(NamingProposal.objects.count(),0)

    def test_form_rejects_inconsistent_conditions_and_sources(self):
        for change in [dict(group_type='all'),dict(match_mode='exact'),dict(match_mode='single_host_ip'),
                       dict(match_field='ObjectType',match_value='NET'),{'components-2-source':'single_host_ip','components-2-format':'ip_address'}]:
            with self.subTest(change=change):
                response = self.client.post(reverse('naming-policies'),dict(self.payload(),**change))
                self.assertEqual(response.status_code,200); self.assertTrue(response.context['form'].errors)
        self.assertEqual(NamingPolicy.objects.count(),1)

    def test_live_example_excludes_hosts_and_multiple_subnets(self):
        self.add('10.0.0.1'); self.add(config=definition('10.0.0.0/24','10.1.0.0/24'))
        example = self.client.post(reverse('naming-example'),self.payload()).json()
        self.assertNotIn('proposed',example)
        self.assertIn('No matching group',example['message'])
