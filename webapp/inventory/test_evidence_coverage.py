from copy import deepcopy
from datetime import timedelta
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from .evidence_coverage import issues, relevant, snapshot_issues
from .models import Environment, Snapshot, Finding, FindingPolicy, SnapshotRecord
from .findings import synchronize
from .finding_workflow import approval_readiness
from . import finding_recalculation as replay
from .test_foundations import report

VM_WARNING = {'endpoint':'/infra/realized-state/virtual-machines', 'detail':'Duplicate row; inventory incomplete'}


class ScopeTests(SimpleTestCase):
    def test_vm_warning_is_not_a_configuration_reference_failure(self):
        coverage = issues({'inventory_warnings':[VM_WARNING]})
        for kind in ('empty_group','tag','membership'):
            self.assertTrue(relevant(coverage, kind, '/a'))
        for kind in ('unused','unused_service','zero_hits','disabled','empty_policy'):
            self.assertFalse(relevant(coverage, kind, '/a'))

    def test_unknown_and_configuration_warnings_fail_closed(self):
        for endpoint in ('/new-endpoint','/infra/services','/search/query','/infra/domains/default/groups'):
            for kind in ('empty_group','unused_service','disabled','zero_hits','empty_policy'):
                self.assertTrue(relevant(issues({'inventory_warnings':[{'endpoint':endpoint}]}), kind, '/a'))

    def test_membership_warning_scoped_to_target_and_reference_errors(self):
        coverage = issues({'inventory_warnings':[{'endpoint':'/infra/domains/default/groups/g/members/virtual-machines'}]})
        self.assertTrue(relevant(coverage,'empty_group','/infra/domains/default/groups/g'))
        self.assertFalse(relevant(coverage,'empty_group','/infra/domains/default/groups/other'))
        self.assertTrue(relevant(issues({'search_coverage':{'failed':True}}),'unused_service','/a'))
        self.assertTrue(relevant(issues({'dfw':{'errors':['incomplete rules']}}),'unused','/a'))


class ScopedPublicationTests(TestCase):
    def setUp(self):
        self.env = Environment.objects.create(name='Scoped',slug='scoped',manager='https://example.test')
        FindingPolicy.objects.create(empty_group_days=0, unused_service_days=0, unused_days=0)
        self.data = report()
        self.data['objects'].append({'kind':'custom_service','path':'/infra/services/test','name':'Service',
                                     'membership':'not_applicable','usage':'unused_candidate','referenced_by':[]})
        self.first = self.snapshot(self.data, -1)
        synchronize(self.env.pk)
        self.group = Finding.objects.get(environment=self.env,kind='empty_group')
        self.group.workflow_state='ready'; self.group.approvals={'owner':{'saved':'decision'}}; self.group.save()

    def snapshot(self, data, minutes=0):
        return Snapshot.objects.create(environment=self.env,generated_at=timezone.now()+timedelta(minutes=minutes),
            report=deepcopy(data),summary={'inventory_warnings':bool(data.get('inventory_warnings')), 'evidence_coverage':issues(data)})

    def test_partial_vm_collection_preserves_group_and_advances_service(self):
        self.data['inventory_warnings']=[VM_WARNING]
        latest=self.snapshot(self.data)
        synchronize(self.env.pk)
        self.group.refresh_from_db()
        self.assertEqual(self.group.snapshot_id,self.first.pk)
        self.assertEqual(self.group.workflow_state,'ready')
        self.assertEqual(self.group.approvals,{'owner':{'saved':'decision'}})
        self.assertTrue(approval_readiness(self.group)['verification_paused'])
        service=Finding.objects.get(environment=self.env,kind='unused_service')
        self.assertEqual(service.snapshot_id,latest.pk)
        SnapshotRecord.objects.create(snapshot=latest,ordinal=1,view='inventory',name='Service',sort_name='service',
            compact=self.data['objects'][1],data={},columns=[],sort_values={},search_basic='',search_evidence='')
        self.assertEqual(approval_readiness(service)['status'],'ready')
        replay.queue([self.env.pk],['empty_group','unused_service'])
        job=replay.claim(); replay.run(job.pk,job.token); job.refresh_from_db()
        self.assertEqual(job.status,'completed')
        self.group.refresh_from_db()
        self.assertEqual(self.group.approvals,{'owner':{'saved':'decision'}})
        self.assertEqual(self.group.snapshot_id,self.first.pk)

    def test_unknown_endpoint_does_not_clear_missing_findings(self):
        latest=self.snapshot({'objects':[], 'inventory_warnings':[{'endpoint':'/unknown'}]})
        synchronize(self.env.pk)
        self.group.refresh_from_db()
        self.assertTrue(self.group.present)
        self.assertEqual(self.group.snapshot_id,self.first.pk)
        self.assertEqual(self.group.workflow_state,'ready')

    def test_legacy_warning_without_details_blocks_all(self):
        latest=self.snapshot({})
        latest.summary={'inventory_warnings':True};latest.save()
        self.assertTrue(relevant(snapshot_issues(latest),'unused_service','/infra/services/test'))

    def test_changed_configuration_invalidates_even_when_vm_verification_paused(self):
        self.group.evidence['configuration']={'display_name':'old'}; self.group.save()
        self.data['objects'][0]['configuration']={'display_name':'new'}
        self.data['inventory_warnings']=[VM_WARNING]
        self.snapshot(self.data);synchronize(self.env.pk)
        self.group.refresh_from_db()
        self.assertEqual(self.group.approvals,{})
        self.assertNotEqual(self.group.workflow_state,'ready')

    def test_recreated_identity_invalidates_decisions_during_pause(self):
        self.group.evidence['unique_id']='original'; self.group.save()
        self.data['objects'][0]['unique_id']='recreated'
        self.data['inventory_warnings']=[VM_WARNING]
        self.snapshot(self.data); synchronize(self.env.pk)
        self.group.refresh_from_db()
        self.assertEqual(self.group.approvals,{})
