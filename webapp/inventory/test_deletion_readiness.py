from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from .deletion_readiness import historical_group_evidence, strict_group_empty
from .evidence_coverage import issues
from .finding_workflow import approval_readiness
from .findings import synchronize
from .models import Environment, FindingPolicy, Finding, Snapshot, SnapshotRecord


class DirectMembershipTests(SimpleTestCase):
    def setUp(self):
        self.group={'path':'/infra/domains/default/groups/g', 'expression':[
            {'resource_type':'Condition','member_type':'VirtualMachine','key':'Tag','operator':'EQUALS','value':'VDI|yes'}]}

    def test_requires_all_three_explicit_empty_results(self):
        client=Mock(); client.get.return_value={'results':[],'result_count':0}
        self.assertTrue(strict_group_empty(client,self.group))
        self.assertEqual(client.get.call_count,3)
        for bad in ({'results':[]}, {'results':[],'result_count':False}, {'results':[],'result_count':0,'cursor':'next'},
                    {'results':[{'id':'member'}],'result_count':1}, {'results':[],'result_count':0,'error_message':'failed'}):
            client.get.side_effect=[{'results':[],'result_count':0},bad]
            self.assertFalse(strict_group_empty(client,self.group))

    def test_composed_or_nested_groups_do_not_use_exception(self):
        for expressions in ([], [{'resource_type':'PathExpression','paths':['/infra/groups/nested']}], self.group['expression']*2):
            client=Mock()
            self.assertFalse(strict_group_empty(client,dict(self.group,expression=expressions)))
            client.get.assert_not_called()


class HistoricalExecutionTests(TestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='Direct',slug='direct',manager='https://nsx.example')
        self.policy=FindingPolicy.objects.create(empty_group_days=0,minimum_observations=2,maximum_gap_hours=24)
        self.config={'resource_type':'Group','path':'/infra/domains/default/groups/g','unique_id':'same',
            'expression':[{'resource_type':'Condition','member_type':'VirtualMachine','key':'Name','operator':'EQUALS','value':'test'}]}
        self.row={'path':self.config['path'],'name':'G','kind':'group','membership':'empty','usage':'used',
                  'configuration':self.config,'unique_id':'same','created_at':1}
        self.source=self.snapshot(-5,[])
        synchronize(self.env.pk)
        self.finding=Finding.objects.get(environment=self.env,kind='empty_group')
        self.finding.workflow_state='ready'; self.finding.save()
        self.warning={'endpoint':'/infra/realized-state/virtual-machines','detail':'Duplicate VM row'}
        self.latest=self.snapshot(-1,[self.warning])
        synchronize(self.env.pk)
        self.finding.refresh_from_db()

    def snapshot(self,minutes,warnings):
        report={'objects':[deepcopy(self.row)],'inventory_warnings':warnings,'search_coverage':{'mode':'all_types'}}
        snap=Snapshot.objects.create(environment=self.env,generated_at=timezone.now()+timedelta(minutes=minutes),report=report,
            summary={'inventory_warnings':bool(warnings),'evidence_coverage':issues(report)})
        SnapshotRecord.objects.create(snapshot=snap,ordinal=1,view='inventory',name='G',sort_name='g',compact={k:deepcopy(v) for k,v in self.row.items() if not isinstance(v,(dict,list))},
            data=deepcopy(self.row),columns=[],sort_values={},search_basic='',search_evidence='')
        return snap

    def test_execution_only_preserves_history_and_normal_approval_block(self):
        count=self.finding.observation_count
        self.assertEqual(approval_readiness(self.finding)['status'],'blocked')
        ready=approval_readiness(self.finding,deletion_live=True)
        self.assertEqual(ready['status'],'ready')
        self.assertTrue(ready['requires_direct_membership'])
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.observation_count,count)
        self.assertEqual(self.finding.snapshot_id,self.source.pk)

    def test_changed_policy_or_stale_history_blocks(self):
        self.policy.empty_group_days=10; self.policy.save()
        self.assertIsNone(historical_group_evidence(self.finding,self.latest))
        self.policy.empty_group_days=0; self.policy.save()
        self.finding.last_seen=timezone.now()-timedelta(days=2)
        self.assertIsNone(historical_group_evidence(self.finding,self.latest))

    def test_missing_changed_or_nonempty_current_evidence_blocks(self):
        record=SnapshotRecord.objects.get(snapshot=self.latest)
        for change in ({'membership':'unknown'},{'membership':'nonempty'},{'unique_id':'new'}, {'configuration':{}}):
            changed=dict(self.row,**change)
            record.compact={k:v for k,v in changed.items() if not isinstance(v,(dict,list))}
            record.data=changed; record.save()
            self.assertIsNone(historical_group_evidence(self.finding,self.latest))
        record.delete()
        self.assertIsNone(historical_group_evidence(self.finding,self.latest))

    def test_other_warning_or_newer_verified_snapshot_blocks(self):
        for warnings in ([{'endpoint':'/search/query'}],[]):
            newer=self.snapshot(0,warnings)
            self.assertIsNone(historical_group_evidence(self.finding,newer))
            newer.delete()
