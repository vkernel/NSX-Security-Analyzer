# Finding review approvals

Finding reviews authorize a proposed decommissioning decision. They never delete
or modify an NSX object. Operators and administrators can perform workflow
actions; viewers can read the results. The same rules apply to local, Keycloak
and LDAP accounts. Each person must use their own account.

## Configure the approval requirement

Open **Administration → Review approvals**, select **Single approval** or
**Two-person approval (four-eyes principle)** and click **Save approval setting**.
Only administrators can change this global setting. Two-person approval remains
the default; existing reviews retain their original requirement. Assignment,
reassignment and reopening capture the current setting for that review.

With single approval, the assigned owner's approval moves the finding directly
to **Ready for decommissioning**. All evidence checks and manual verification
requirements still apply. Record completion with a change ticket as described
below. Approval requirements and decisions are retained in the audit history.

## Two-person workflow (default)

1. Open an eligible finding and choose **Assign owner**. Select an active operator
   or administrator and provide a reason. The finding enters **Owner review**.
2. The owner manually checks dependencies, coverage and change impact. Choose
   **Approve review** or **Reject finding** and describe the checks and decision.
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

Every required approval requires an eligible, currently observed finding from the latest
full, non-testing, non-imported snapshot. The snapshot must be within the shared
freshness threshold (Administration → Freshness & notifications). Approval checks
use the selected object's saved indexed evidence:

| Finding | Required evidence |
| --- | --- |
| Empty group | Membership successfully confirmed empty |
| Zero-hit rule | Fresh zero counters with successful zero-hit status; no disabled rule |
| Unused object | Unused candidate with unrestricted search, or successful compatibility search plus independent manual verification by each required reviewer |
| Disabled rule | Explicitly confirmed disabled state |
| Empty policy | Successful empty status and exactly zero rules |

Missing indexed object evidence, exclusions, unknown or contradictory values block
approval with a specific explanation. Unrecorded or failed search blocks unused-object approvals. Successful compatibility
search permits a documented manual verification route: each reviewer must confirm
an independent dependency check, describe the systems and dependencies checked
outside the collected search scope, and provide a ticket or evidence reference.
The first reviewer’s confirmation never substitutes for the second person’s check.
Coverage remains limited; approval does not relabel it as complete. Search
remains eventually consistent and visibility depends on permissions; unrestricted
search is not proof that every possible dependency is visible.

Warnings about unrelated objects and an unprepared global coverage summary no
longer block a finding whose own relevant evidence is complete. They remain visible
on Collection coverage. These same finding-specific checks apply when a new
collection revalidates pending approvals. Existing indexed snapshots benefit without
a new collection; missing object indexes must still be prepared or recollected.

Each approval retains the actor identity, timestamp, reason, source snapshot UUID,
relevant evidence fingerprint, evidence, reference-search scope and any manual
verification description and evidence reference. A changed reference-search scope
requires a new owner review. A routine collection with unchanged
relevant evidence preserves the approvals. Relevant changes, a disappeared
condition, incomplete evidence relevant to the finding or criteria recalculation that removes
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

## Click-by-click example with Keycloak users

### Prepare the two reviewers

Have both people sign in to NSX Security Analyzer using **Sign in with Keycloak**
at least once. Their Keycloak role mappings must grant the application **Operator**
or **Administrator** role. Viewer accounts cannot be assigned or approve findings.
The owner selector searches users already provisioned in this application, not the
entire Keycloak directory. It displays **Name · email · Keycloak**. LDAP and local
accounts show their corresponding provider. If names or email are missing, correct
the identity-provider profile/claims and sign in again to refresh the profile.

### 1. Assign the finding

1. Open **Environments**, select your environment, then click **Finding reviews**.
2. Click the finding's name to open its detail page.
3. In the right-hand decision panel, expand **Assign owner** (or **Reassign owner**).
4. Type the person's name or email in **Search owners**, then select the correct
   person in **Owner**. Search filters the list; it does not choose an owner for
   you. A previously selected owner remains selected until explicitly changed.
5. Enter the assignment reason in **Reason / manual checks performed** and click
   **Assign owner**. The stage becomes **Owner review**.

### 2. Owner performs the first review

1. The assigned owner signs in and opens **Finding reviews → My reviews**, or uses
   the assignment notification in the notification bell.
2. Open the finding. Use **Open latest evaluated snapshot** and **Technical evidence
   → Load saved technical evidence** to inspect the relevant information.
   Check dependencies and business requirements manually.
3. Read the readiness banner. Write your reasoning in **Reason / manual checks
   performed**. If manual verification is required, complete the confirmation,
   dependency-check description and evidence reference. Click **Approve review**
   or **Reject finding**. Rejection does not require the approval confirmation.
4. Approval moves it to **Awaiting second approval**. Rejection moves it to
   **Rejected**, retaining the reason in history.

### 3. Another person performs the independent review

1. A different Operator or Administrator signs in.
2. Open the environment's **Finding reviews → Awaiting second approval**, or open
   its review notification. Select the finding.
3. Read the owner approval and inspect the evidence independently.
4. Enter a reason and, where required, your own independent verification details.
   Click **Approve review** or **Reject finding**.
   The owner cannot provide this approval, including when the owner is an admin.
5. Second approval moves the finding to **Ready for decommissioning**. This is a
   decision recorded in the analyzer; no object is deleted automatically.

### 4. Perform and record the change

1. Open **Finding reviews → Ready for decommissioning** and select the finding.
2. Execute the actual change using your normal change-management process.
3. The decision panel now shows **Change-ticket reference**. Enter your ticket identifier and describe what was done in
   **Reason / manual checks performed**.
4. Click **Record decommissioning**. The finding becomes **Decommissioned**. Record this
   promptly, before a new collection removes the condition and invalidates the
   pending approval. Approval/completion guards described above still apply.

### 5. Inspect the audit trail

Scroll to **Review history** on the finding page. It shows the actor, time and
reason for each action. Operators and administrators can click **Export audit
history (JSON)** to download the decisions with saved evidence and stable actor
IDs. Names are for display; changing a name or email cannot bypass the different-
approver check. Existing historical identity values are preserved in the export;
the page resolves older internal Keycloak identifiers to readable names when the
account still exists.

The owner selector is in the expandable assignment section. **Change-ticket reference**
appears only when recording completion. To change notification preferences, open your user menu →
**Personal settings → Notifications** and change **Finding assignments and second
approvals**.

## Reading the review page

The progress indicator shows the current stage. Evidence and dependencies appear
on the left; the owner, next action and decision controls appear on the right
(or below on smaller screens). Observation criteria being met does not mean
approval is permitted: the readiness banner separately identifies missing evidence
or required manual verification before submission. Technical evidence and detailed
observation dates are expandable. Validation errors preserve entered notes.

Owner and second approvals show their reasons and independent verification
records. Review history is a chronological activity list with actor names and
timestamps; use the JSON export for the complete retained decision records.

## Daily review queue

Open **My work** in the main menu to see tasks across environments:

- **Assigned to me** contains your owner reviews.
- **Available for second approval** excludes your own first approvals.
- **Ready for decommissioning** contains approved findings awaiting a recorded change.

The findings list shows whether each review requires one or two approvals. To assign
an owner, expand the assignment section, search by name or email, choose a result and
record a reason. Search returns up to 20 active operators/administrators; refine your
search for more specific matches. External users still need to sign in once before
they can be selected. A second approval remains available to another eligible reviewer;
it does not require reassigning the owner.

Administration uses fixed category tabs: Access, Finding reviews, Collections, and
Operations. A second row shows the pages in the selected category. Open Finding reviews to change criteria or approval settings.

## Navigation and review feedback

Administration Overview is a settings directory. Open **Collections → Environments &
sync** for schedules; **Freshness & notifications** has the same name throughout the
application. Environment analysis tabs highlight the current page, including Snapshots.
Reviewer assignment uses one server-side search by name or email.

A review queue distinguishes no eligible findings from no filter matches, and offers
Clear filters for the latter. Comparisons show Queued, Preparing, Failed (with Retry),
or Ready. Recent collections use aligned columns on desktop and labelled cards on
mobile, retaining progress updates and stop controls for active collections.
