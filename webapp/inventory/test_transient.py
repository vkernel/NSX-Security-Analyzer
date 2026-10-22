from copy import deepcopy
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, Finding, FindingPolicy, TransientEmptinessPolicy
from .transient import matching, condition_scope
from .findings import synchronize
from .finding_workflow import approval_readiness
from . import finding_recalculation as replay
from .test_foundations import report


def config(scope='VDI_Gebruiker'):
    return {'expression':[{'resource_type':'Condition','member_type':'VirtualMachine',
                          'key':'Tag','operator':'EQUALS','value':scope+'|123'}]}


class MatchingTests(SimpleTestCase):
    def test_only_positive_same_scope_vm_conditions(self):
        rule = TransientEmptinessPolicy(name='VDI', tag_scope='VDI_Gebruiker', enabled=True)
        self.assertEqual(matching({'configuration':config()},[rule]),rule)
        for scope in ('vdi_gebruiker','VDI_Gebruiker2'):
            self.assertIsNone(matching({'configuration':config(scope)},[rule]))
        for field,value in [('operator','NOT_EQUALS'),('member_type','Segment'),('key','Name'),('resource_type','NestedExpression')]:
            data=config();data['expression'][0][field]=value
            self.assertIsNone(condition_scope({'configuration':data}))
        data=config();data['expression'] += [{'resource_type':'ConjunctionOperator','conjunction_operator':'OR'}]+config('other')['expression']
        self.assertIsNone(condition_scope({'configuration':data}))
        self.assertIsNone(condition_scope({'configuration':dict(config(),extended_expression=[{}])}))


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class TransientPolicyTests(TestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='VDI',slug='vdi',manager='https://nsx.example')
        self.admin=get_user_model().objects.create_superuser('policy-admin',password='test-only')
        FindingPolicy.objects.create(empty_group_days=0, unused_days=0)
        data=report();data['objects'][0]['membership']='empty';data['objects'][0]['configuration']=config()
        self.snap=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report=data)
        synchronize(self.env.pk)
        self.finding=Finding.objects.get(environment=self.env,kind='empty_group')
        self.url=reverse('transient-emptiness')
        self.payload={'name':'Session VDI','enabled':'on','environment':str(self.env.pk),'tag_scope':'VDI_Gebruiker','reason':'Session-driven membership'}

    def test_admin_preview_save_and_disable_preserve_history(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.post(self.url,dict(self.payload,action='preview')),'Preview: 1 matching groups')
        self.assertFalse(TransientEmptinessPolicy.objects.exists())
        self.finding.owner=self.admin;self.finding.workflow_state='second_review';self.finding.approvals={'owner':{'actor_id':str(self.admin.pk)}};self.finding.save()
        self.assertEqual(self.client.post(self.url,dict(self.payload,action='save')).status_code,302)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.qualification,'suppressed')
        self.assertEqual(self.finding.owner,self.admin)
        self.assertEqual(self.finding.approvals,{})
        self.assertTrue(self.finding.events.exists())
        self.assertEqual(approval_readiness(self.finding)['status'],'blocked')
        job=replay.claim();replay.run(job.pk,job.token)
        self.finding.refresh_from_db();self.assertEqual(self.finding.qualification,'suppressed')
        response=self.client.get(reverse('findings',args=[self.env.pk]),{'review':'suppressed'})
        self.assertContains(response,self.finding.name)
        policy=TransientEmptinessPolicy.objects.get()
        payload=dict(self.payload,id=policy.pk,action='save');payload.pop('enabled')
        self.client.post(self.url,payload)
        job=replay.claim();replay.run(job.pk,job.token)
        self.finding.refresh_from_db();self.assertEqual(self.finding.qualification,'eligible')
        self.assertEqual(self.finding.approvals,{})
        self.snap.refresh_from_db();self.assertEqual(self.snap.report['objects'][0]['membership'],'empty')

    def test_permissions_environment_scope_and_collection(self):
        reader=get_user_model().objects.create_user('reader')
        self.client.force_login(reader)
        self.assertEqual(self.client.post(self.url,dict(self.payload,action='save')).status_code,403)
        other=Environment.objects.create(name='Other',slug='other',manager='https://other')
        TransientEmptinessPolicy.objects.create(name='Other',environment=other,tag_scope='VDI_Gebruiker',reason='test')
        self.assertNotIn('transient',approval_readiness(self.finding)['message'])
        TransientEmptinessPolicy.objects.create(name='Global',tag_scope='VDI_Gebruiker',reason='test')
        self.assertIn('transient',approval_readiness(self.finding)['message'])
        self.finding.present=False;self.finding.save(update_fields=['present'])
        new=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report=deepcopy(self.snap.report))
        synchronize(self.env.pk)
        self.finding.refresh_from_db();self.assertEqual(self.finding.qualification,'suppressed')
        self.assertFalse(Finding.objects.filter(kind='unused',qualification='suppressed').exists())

    def test_monitor_gui_thresholds_replay_and_returning_members(self):
        from datetime import timedelta
        self.client.force_login(self.admin)
        payload = dict(self.payload, action='save', mode='monitor', empty_days=2,
                       minimum_observations=3, maximum_gap_hours=48)
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        policy = TransientEmptinessPolicy.objects.get()
        self.assertEqual((policy.mode, policy.empty_days, policy.minimum_observations, policy.maximum_gap_hours), ('monitor', 2, 3, 48))
        job = replay.claim(); replay.run(job.pk, job.token)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.qualification, 'observing')
        self.assertEqual(self.finding.required_days, 2)
        for day in (1, 2):
            Snapshot.objects.create(environment=self.env, generated_at=self.snap.generated_at + timedelta(days=day), report=deepcopy(self.snap.report))
            synchronize(self.env.pk)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.qualification, 'eligible')
        self.assertEqual(self.finding.observation_count, 3)
        self.assertIn('Empty in every qualifying observation', self.finding.qualification_reason)
        replay.queue([self.env.pk], ['empty_group']); job = replay.claim(); replay.run(job.pk, job.token)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.qualification, 'eligible')
        self.assertEqual(self.finding.observation_count, 3)
        self.assertContains(self.client.get(reverse('findings', args=[self.env.pk])), 'Prolonged empty VDI group')
        self.finding.owner = self.admin
        self.finding.workflow_state = 'second_review'
        self.finding.approvals = {'owner': {'actor_id': str(self.admin.pk)}}
        self.finding.save()
        data = deepcopy(self.snap.report); data['objects'][0]['membership'] = 'nonempty'
        Snapshot.objects.create(environment=self.env, generated_at=self.snap.generated_at + timedelta(days=3), report=data)
        synchronize(self.env.pk)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.qualification, 'cleared')
        self.assertEqual(self.finding.approvals, {})
        self.assertEqual(self.finding.owner, self.admin)
        Snapshot.objects.create(environment=self.env, generated_at=self.snap.generated_at + timedelta(days=4), report=deepcopy(self.snap.report))
        synchronize(self.env.pk)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.qualification, 'observing')
        self.assertEqual(self.finding.observation_count, 1)

    def test_monitor_unknown_and_gap_restart_in_live_and_replay(self):
        from datetime import timedelta
        TransientEmptinessPolicy.objects.create(name='Monitor', tag_scope='VDI_Gebruiker',
            mode='monitor', empty_days=2, minimum_observations=3, maximum_gap_hours=48)
        for day, membership in [(1, 'empty'), (2, 'unknown'), (3, 'empty'), (7, 'empty')]:
            data = deepcopy(self.snap.report); data['objects'][0]['membership'] = membership
            Snapshot.objects.create(environment=self.env, generated_at=self.snap.generated_at + timedelta(days=day), report=data)
            synchronize(self.env.pk)
            if day in (3, 7):
                self.finding.refresh_from_db()
                self.assertEqual(self.finding.observation_count, 1)
                self.assertEqual(self.finding.qualification, 'observing')
        replay.queue([self.env.pk], ['empty_group']); job = replay.claim(); replay.run(job.pk, job.token)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.observation_count, 1)
        self.assertEqual(self.finding.qualification, 'observing')

    def test_monitor_requires_valid_thresholds(self):
        from .transient import PolicyForm
        self.assertFalse(PolicyForm(dict(self.payload, mode='monitor')).is_valid())
        self.assertFalse(PolicyForm(dict(self.payload, mode='monitor', empty_days=0, minimum_observations=1, maximum_gap_hours=0)).is_valid())
