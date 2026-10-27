"""Close sibling findings only after verified object absence; preserve approvals."""
from .models import Finding
from .finding_workflow import event


def related_findings(primary, *, lock=False):
    evidence = primary.evidence
    if not evidence.get('unique_id') or evidence.get('created_at') is None:
        return Finding.objects.none()
    rows = Finding.objects.filter(environment_id=primary.environment_id, path=primary.path,
        evidence__unique_id=evidence['unique_id'], evidence__created_at=evidence['created_at']).exclude(
        pk=primary.pk).exclude(workflow_state='decommissioned').order_by('pk')
    return rows.select_for_update() if lock else rows


def close_related(primary, actor, *, deletion_item=None):
    """Caller holds environment lock and has verified absence in NSX."""
    closed = []
    for sibling in related_findings(primary, lock=True):
        before = sibling.workflow_state
        sibling.workflow_state = 'decommissioned'
        sibling.present = False
        sibling.change_ticket = primary.change_ticket
        sibling.revision += 1
        sibling.save(update_fields=['workflow_state', 'change_ticket', 'revision', 'present'])
        extra = {'primary_finding': primary.pk, 'change_ticket': primary.change_ticket,
                 'absence_verified': True}
        if deletion_item:
            extra.update(deletion_item=deletion_item.pk, deletion_batch=str(deletion_item.batch_id))
        event(sibling, 'related_decommissioned', before,
              f'Decommissioned through finding #{primary.pk}; object absence verified. Original approvals retained.',
              actor, extra)
        closed.append(sibling.pk)
    if closed:
        event(primary, 'related_findings_closed', 'decommissioned',
              'Related findings closed after verified object absence.', actor, {'related_findings': closed})
    return closed
