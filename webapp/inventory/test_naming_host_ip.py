from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import naming
from .models import Environment, NamingPolicy, NamingProposal, Snapshot, SnapshotRecord
from .naming_forms import ComponentForm


def host_config():
    base = dict(scope='', literal='', required=True, case='keep', format='extended', max_length=64, mapping={})
    return dict(target='group', group_type='static', match_mode='single_host_ip', match_field='', match_value='', separator='-', max_length=128,
                components=[dict(base, field='Prefix', source='fixed', literal='SZ-TZW-EXT'),
                            dict(base, field='ObjectType', source='fixed', literal='HST'),
                            dict(base, field='Address', source='single_host_ip', format='ip_address')])


def definition(*addresses):
    return {'expression': [{'resource_type': 'IPAddressExpression', 'ip_addresses': list(addresses)}]}


class ExplicitHostIPTests(SimpleTestCase):
    def test_canonical_host_addresses_and_host_prefixes(self):
        for value, expected in [('10.128.196.150','10.128.196.150'), ('10.128.196.150/32','10.128.196.150'),
                                ('2001:0DB8:0:0::1','2001:db8::1'), ('2001:db8::1/128','2001:db8::1')]:
            with self.subTest(value=value):
                self.assertEqual(naming.explicit_host_ip({'definition': definition(value)}), expected)

    def test_unsupported_or_ambiguous_membership_is_never_a_host(self):
        cases = [None, {}, definition(), definition('10.0.0.1','10.0.0.2'), definition('10.0.0.1','10.0.0.1'),
                 definition('10.0.0.0/24'), definition('10.0.0.1/24'), definition('2001:db8::/64'),
                 definition('10.0.0.1-10.0.0.2'), definition('010.000.000.001'), definition('invalid'),
                 definition('fe80::1%eth0'), definition(123), {'expression':[None]},
                 {'expression':[{'resource_type':'IPAddressExpression','ip_addresses':None}]},
                 {'expression':[{'resource_type':'PathExpression','paths':['/infra/groups/child']}]},
                 {'expression':[{'resource_type':'ExternalIDExpression','external_ids':['vm-1']}]},
                 {'expression': definition('10.0.0.1')['expression'] + [{'resource_type':'IPAddressExpression','ip_addresses':[]}]},
                 {'expression': definition('10.0.0.1')['expression'] + [{'resource_type':'Condition','key':'Tag'}]},
                 dict(definition('10.0.0.1'), extended_expression=[{'resource_type':'IdentityGroupExpression'}])]
        for value in cases:
            with self.subTest(definition=value):
                self.assertIsNone(naming.explicit_host_ip({'definition':value}))

    def test_invalid_explicit_membership_is_reported_without_crashing(self):
        evidence=naming.group_evidence({'expression':[{'resource_type':'IPAddressExpression','ip_addresses':None}]})
        self.assertTrue(evidence['needs_review'])

    def test_host_component_requires_ip_format_and_cannot_map_away_membership(self):
        data=dict(field='Address',source='single_host_ip',required=True,case='keep',format='ip_address',max_length=45)
        self.assertTrue(ComponentForm(data).is_valid())
        for change, field in [({'case':'upper'},'case'),({'format':'alphanumeric'},'format'),({'required':False},'required'),
                              ({'mapping_text':'10.0.0.1=10.0.0.2'},'mapping_text')]:
            with self.subTest(change=change):
                form=ComponentForm(dict(data,**change));self.assertFalse(form.is_valid());self.assertIn(field,form.errors)


@override_settings(STORAGES={'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class HostNamingPolicyTests(TestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='Zwolle',slug='host-naming',manager='https://offline.invalid')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),summary={},report={})
        self.config=host_config()
        self.policy=NamingPolicy.objects.create(name='Zwolle static hosts',environment=self.env,enabled=True,configuration=self.config,draft=self.config)
        self.user=get_user_model().objects.create_superuser('host-admin',password='offline-test-only')
        self.client.force_login(self.user)

    def add(self, ip='10.128.196.150', name=None, config=None):
        path='/infra/domains/default/groups/host-'+str(SnapshotRecord.objects.count())
        return SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=SnapshotRecord.objects.count(),view='inventory',name=name or 'SZ-TZW-EXT-HST-'+ip,
            sort_name=name or ip,compact={},data={'kind':'group','path':path,'configuration':config or definition(ip)},columns=[],sort_values={},search_basic='',search_evidence='')

    def test_150_groups_are_evaluated_without_any_component_proposals(self):
        for number in range(1,151):
            self.add('10.128.196.'+str(number),name='incorrect-name' if number==100 else None)
        rows=naming.assess(self.env)[1]
        self.assertEqual(naming.queue_counts(rows),{'all':150,'compliant':149,'attention':1,'exceptions':0})
        changed=next(row for row in rows if row['name']=='incorrect-name')
        self.assertEqual(changed['proposed'],'SZ-TZW-EXT-HST-10.128.196.100')
        self.assertTrue(all(row['components'][-1]['source']=='Explicit IP membership' for row in rows))
        self.assertEqual(NamingProposal.objects.count(),0)
        self.assertEqual(naming.assess(self.env)[1][0]['proposed'],rows[0]['proposed'])

    def test_reviewed_values_cannot_override_explicit_membership(self):
        record=self.add();row=naming.assess(self.env)[1][0]
        NamingProposal.objects.create(environment=self.env,group_path=row['identity'],object_type='group',baseline=naming.baseline(row),state='reviewed',tags={'naming/address':'203.0.113.99'})
        self.assertEqual(naming.assess(self.env)[1][0]['proposed'],'SZ-TZW-EXT-HST-10.128.196.150')
        record.data['configuration']=definition('10.128.196.151');record.save()
        changed=naming.assess(self.env)[1][0]
        self.assertEqual(changed['proposed'],'SZ-TZW-EXT-HST-10.128.196.151')
        self.assertEqual(changed['status'],'Name differs')

    def test_nonhost_groups_are_excluded_and_general_static_fallback_still_works(self):
        self.add(config=definition('10.0.0.0/24'))
        self.assertEqual(naming.assess(self.env)[1][0]['status'],'No applicable policy')
        fallback=host_config();fallback.update(match_mode='exact');fallback['components']=fallback['components'][:1]
        other=NamingPolicy.objects.create(name='Static fallback',environment=self.env,enabled=True,configuration=fallback,draft=fallback)
        self.assertEqual(naming.assess(self.env)[1][0]['policy'].pk,other.pk)
        self.add()
        self.assertEqual(naming.assess(self.env)[1][1]['policy'].pk,self.policy.pk)

    def test_equally_specific_host_policies_are_ambiguous(self):
        self.add()
        NamingPolicy.objects.create(name='Other host policy',environment=self.env,enabled=True,configuration=self.config,draft=self.config)
        self.assertEqual(naming.assess(self.env)[1][0]['status'],'Ambiguous policy')

    def test_existing_exact_category_policy_is_not_silently_overridden(self):
        self.add();row=naming.assess(self.env)[1][0]
        manual=host_config();manual.update(match_mode='exact',match_field='ObjectType',match_value='HST')
        manual['components'][1].update(source='tag',scope='naming/object-type')
        manual['components'][2].update(source='tag',scope='naming/address')
        NamingPolicy.objects.create(name='Existing manual hosts',environment=self.env,enabled=True,configuration=manual,draft=manual)
        NamingProposal.objects.create(environment=self.env,group_path=row['identity'],object_type='group',baseline=naming.baseline(row),state='reviewed',tags={'naming/object-type':'HST','naming/address':'10.128.196.150'})
        self.assertEqual(naming.assess(self.env)[1][0]['status'],'Ambiguous policy')

    def test_ipv6_and_host_cidr_are_derived_from_definition(self):
        self.add('2001:db8::1',config=definition('2001:0DB8::1/128'))
        self.add('10.128.196.151',config=definition('10.128.196.151/32'))
        self.assertTrue(all(row['status']=='Compliant' for row in naming.assess(self.env)[1]))

    def payload(self):
        data=dict(action='save',name='Automatic host draft',environment=self.env.pk,group_type='static',match_mode='single_host_ip',separator='-',max_length=128,revision=0,
                  **{'components-TOTAL_FORMS':3,'components-INITIAL_FORMS':0,'components-MIN_NUM_FORMS':1,'components-MAX_NUM_FORMS':16})
        for index, component in enumerate(self.config['components']):
            for key,value in component.items():
                if key=='mapping': key,value='mapping_text',''
                data[f'components-{index}-{key}']=value
            data[f'components-{index}-ORDER']=index+1
        return data

    def test_save_preview_activate_and_live_example_need_no_proposals(self):
        self.add();self.policy.enabled=False;self.policy.save()
        data=self.payload()
        example=self.client.post(reverse('naming-example'),data).json()
        self.assertEqual(example['proposed'],'SZ-TZW-EXT-HST-10.128.196.150')
        self.assertEqual(example['components'][-1]['source'],'Explicit IP membership')
        response=self.client.post(reverse('naming-policies'),data)
        self.assertEqual(response.status_code,302)
        policy=NamingPolicy.objects.get(name='Automatic host draft');url=reverse('naming-policy-edit',args=[policy.pk])
        self.assertNotContains(self.client.get(url),'Legacy VM policy:')
        preview=self.client.post(url,{'action':'preview'})
        self.assertEqual(preview.context['preview']['summaries'][0]['policy_counts']['compliant'],1)
        self.assertNotContains(preview,'Legacy VM policy:')
        self.assertEqual(self.client.post(url,{'action':'activate','acknowledge':'yes'}).status_code,302)
        self.assertEqual(naming.assess(self.env)[1][0]['policy'].pk,policy.pk)
        self.assertEqual(NamingProposal.objects.count(),0)

    def test_form_rejects_unsafe_or_inconsistent_conditions(self):
        for change in [dict(group_type='all'),dict(match_mode='exact'),dict(match_field='ObjectType',match_value='HST')]:
            with self.subTest(change=change):
                response=self.client.post(reverse('naming-policies'),dict(self.payload(),**change))
                self.assertEqual(response.status_code,200)
                self.assertTrue(response.context['form'].errors)
        self.assertEqual(NamingPolicy.objects.count(),1)

    def test_live_example_does_not_present_subnet_as_host_or_request_manual_values(self):
        self.add(config=definition('10.0.0.0/24'))
        example=self.client.post(reverse('naming-example'),self.payload()).json()
        self.assertNotIn('proposed',example)
        self.assertNotIn('Prepare component values',example['message'])
