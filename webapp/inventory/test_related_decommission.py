from copy import deepcopy
from unittest.mock import patch
from django.db import transaction
from .test_deletion import DeletionTests
from .models import Finding
from .related_decommission import close_related, related_findings


class RelatedDecommissionTests(DeletionTests):
    def sibling(self, **changes):
        values = dict(environment=self.env, kind='unused', path=self.finding.path, name='Related',
            evidence=deepcopy(self.evidence), first_seen=self.finding.first_seen, last_seen=self.finding.last_seen,
            evaluated_at=self.finding.evaluated_at, workflow_state='unassigned', approvals={})
        values.update(changes)
        return Finding.objects.create(**values)

    def test_closure_links_and_preserves_approvals_and_ignores_new_identity(self):
        sibling=self.sibling(approvals={'old':'decision'})
        recreated=self.sibling(kind='empty_group', evidence=dict(self.evidence, unique_id='new'))
        self.assertEqual(list(related_findings(self.finding)), [sibling])
        self.finding.workflow_state='decommissioned'; self.finding.change_ticket='CHG-1'; self.finding.save()
        with transaction.atomic():
            self.assertEqual(close_related(self.finding,self.user,deletion_item=self.item), [sibling.pk])
            self.assertEqual(close_related(self.finding,self.user,deletion_item=self.item), [])
        sibling.refresh_from_db(); recreated.refresh_from_db()
        self.assertEqual(sibling.workflow_state,'decommissioned')
        self.assertEqual(sibling.approvals,{'old':'decision'})
        self.assertEqual(sibling.events.first().details['primary_finding'], self.finding.pk)
        self.assertEqual(sibling.events.first().details['deletion_batch'], str(self.batch.pk))
        self.assertEqual(recreated.workflow_state,'unassigned')

    def test_uncertain_delete_does_not_close_sibling(self):
        from . import deletion as d
        sibling=self.sibling()
        with patch.object(d,'guard'), patch.object(d,'connection') as conn, patch.object(d,'validate_live',return_value=self.obj), patch.object(d,'current_object',return_value=self.obj), patch.object(d,'delete_once',return_value=202):
            d.process_item(self.item,execute=True)
        sibling.refresh_from_db(); self.item.refresh_from_db()
        self.assertEqual(sibling.workflow_state,'unassigned')
        self.assertEqual(self.item.status,'uncertain')

    def test_successful_delete_closes_sibling(self):
        from . import deletion as d
        sibling=self.sibling()
        with patch.object(d,'guard'), patch.object(d,'connection'), patch.object(d,'validate_live',return_value=self.obj), patch.object(d,'current_object',side_effect=[self.obj,None]), patch.object(d,'delete_once',return_value=204):
            d.process_item(self.item,execute=True)
        sibling.refresh_from_db(); self.item.refresh_from_db()
        self.assertEqual(self.item.status,'deleted')
        self.assertEqual(sibling.workflow_state,'decommissioned')

    def test_manual_completion_only_closes_related_after_live_absence(self):
        from .finding_workflow import decide
        from . import deletion as d
        for absent in (False, True):
            with self.subTest(absent=absent):
                sibling=self.sibling()
                self.finding.workflow_state='ready'; self.finding.save()
                with transaction.atomic(), patch('inventory.finding_workflow.approval_readiness',return_value={'status':'ready','coverage':{}}), patch('inventory.finding_workflow.coverage_changed',return_value=False), patch.object(d,'connection'), patch.object(d,'current_object',return_value=None if absent else self.obj), patch.object(d,'delete_once') as delete:
                    decide(self.finding, self.user, 'complete', 'Removed manually', ticket='CHG-manual')
                sibling.refresh_from_db()
                self.assertEqual(sibling.workflow_state, 'decommissioned' if absent else 'unassigned')
                delete.assert_not_called()
                sibling.delete()

    def test_preview_lists_related_findings(self):
        from . import deletion as d
        from django.urls import reverse
        sibling=self.sibling()
        with patch.object(d,'guard'), patch.object(d,'connection'), patch.object(d,'validate_live',return_value=self.obj):
            d.process_item(self.item)
        self.item.refresh_from_db()
        self.assertEqual(self.item.verification['Related findings'][0]['id'],sibling.pk)
        self.assertContains(self.client.get(reverse('deletion-detail',args=[self.batch.pk])), 'Related findings that will also close')

    def test_only_confirmed_deletion_evidence_suppresses_matching_identity(self):
        from .deleted_objects import identities, superseded
        self.item.status = 'uncertain'; self.item.save()
        self.assertFalse(superseded(identities(self.env.pk), self.finding.path, self.evidence))
        self.item.status = 'deleted'; self.item.save()
        deleted = identities(self.env.pk)
        self.assertTrue(superseded(deleted, self.finding.path, self.evidence))
        self.assertTrue(superseded(deleted, self.finding.path, {}))
        self.assertFalse(superseded(deleted, self.finding.path, dict(self.evidence, unique_id='new')))
        self.assertFalse(superseded(identities(self.env.pk+1000), self.finding.path, self.evidence))
