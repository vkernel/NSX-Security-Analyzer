# Two-person finding reviews

Finding reviews authorize a proposed decommissioning decision. They never delete
or modify an NSX object. Operators and administrators can perform workflow
actions; viewers can read the results. The same rules apply to local, Keycloak
and LDAP accounts. Each person must use their own account.

1. Open an eligible finding and choose **Assign owner**. Select an active operator
   or administrator and provide a reason. The finding enters **Owner review**.
2. The owner manually checks dependencies, coverage and change impact. Choose
   **Approve** or **Reject** and describe the checks and decision.
3. An approval enters **Awaiting second approval**. A different operator or
   administrator performs an independent review. The owner cannot give this
   approval, even with administrator access.
4. Second approval moves the finding to **Ready for decommissioning**. Perform
   the actual change through your organization's change process.
5. Choose **Record decommissioning**, enter the mandatory change-ticket reference
   and completion reason. This records a manual attestation, not automated proof
   of deletion. Record completion before a new collection removes the condition;
   otherwise its approval is invalidated and it must be reviewed again.

A rejection ends the current review. **Reopen review** starts a new owner review
with a mandatory explanation. Reassigning to another owner also clears approvals.
Existing Open/Acknowledged records are migrated to Unassigned or Owner review
according to their owner; old acknowledgements never count as approvals.

## Evidence and validity

Both approvals require an eligible, currently observed finding from the latest
full, non-testing, non-imported snapshot. The snapshot must be within the shared
freshness threshold (Administration → Freshness & notifications) and have a
prepared coverage summary with no incomplete checks. This conservative initial
policy blocks approval even if a coverage issue concerns another object.

Each approval retains the actor identity, timestamp, reason, source snapshot UUID,
relevant evidence fingerprint and evidence. A routine collection with unchanged
relevant evidence preserves the approvals. Relevant changes, a disappeared
condition, incomplete collection evidence or criteria recalculation that removes
eligibility invalidate pending approvals. The owner must review again. Stale data
blocks final approval and completion until a fresh full collection is available.

## Queues, notifications and audit

Use **My reviews**, **Awaiting second approval**, **Ready for decommissioning**,
**Rejected** and **Decommissioned** above the filters. Status sorting uses workflow
stage. Assigned findings remain visible if their evidence stops qualifying, so the
owner can see why a new collection or review is needed. Rejected and completed decisions remain available even if the condition
later disappears. Direct finding URLs retain the full history.

The notification bell includes assignments for the owner and second approvals for
other operators/administrators. Turn these off with the personal **Finding
assignments and second approvals** setting. This setting applies independently of
collection notification overrides. Marking notifications read does not dismiss
pending work from the review queues.

Each assignment, decision, reset and completion writes a review-history event and
an application audit event in the same transaction as the action. Conflicting
edits are rejected; reload the page to see the current decision. Failed workflow
actions are recorded in the application audit log. **Export audit history (JSON)**
includes the saved decisions and their evidence. Review history survives snapshot
retention and retains actor names after user deletion. Deleting an environment
also deletes its finding history; application audit events follow their separate
audit retention policy. Export records before deleting an environment when longer
retention is required.

Deploy the application migrations before starting the updated web and worker
containers. No new service or scheduled task is required.
