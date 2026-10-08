# Snapshot history, finding reviews and coverage

Available from each **Environment** page in version **0.2.0**.

## Compare snapshots

Choose **Compare snapshots**, then select earlier and later full snapshots from
that environment. Results list added, removed and changed rules, groups and
services with field-level before/after values. Testing and imported snapshots
cannot be used. Counters and observation timestamps are excluded from configuration
comparisons; use Historical firewall activity to investigate traffic counters.

New collections retain configuration already returned by NSX, including service
entries and rule/policy configuration. This adds no API requests. Existing
snapshots are not rewritten: unavailable fields appear as **Not recorded**.
Missing inventory under incomplete coverage is labeled **Presence uncertain**.
An added/removed row describes saved inventory, not a verified creation/deletion
event in NSX. A path reused for a recreated object can appear as changed identity.

Membership comparison covers configured expressions and the checked empty,
nonempty or unknown status. It does not enumerate additions/removals in resolved
VM, IP or port member lists: those lists are not downloaded by this collector.
Unknown membership remains unknown, never assumed empty.

## Finding reviews

**Finding reviews** lists currently observed empty groups, unused groups/services,
zero-hit firewall rules, empty firewall policies and disabled rules only after they
meet the configured waiting period. Use Search, Finding type, Review state and
Owner to filter; sort by Name, Finding type or Review status in either direction.
Date and observation controls are no longer part of this page. Unknown or excluded
checks remain in Collection coverage rather than this review queue.

Staff can assign an active user, acknowledge/reopen a finding, set a review date
and append a note in its detail page. Viewers can read reviews. Results are paginated.
Objects still accumulating observations remain visible in inventory, but are not
listed for review. Existing notes are preserved when an object leaves the queue.

Each full collection updates review evidence. Pre-upgrade snapshots establish a
baseline when the review page is first opened. Repeated equivalent evidence
preserves acknowledgement. Relevant configuration/classification changes, or a
finding returning after absence, reopen it while preserving its owner, due date
and notes. New evidence fields collected after an upgrade may also reopen reviews.
A stale edit form is rejected rather than overwriting another review or collection.

Acknowledgement is a workflow decision, not deletion approval or complete audit
coverage. A finding missing from a subsequent snapshot is **not observed**, not
resolved: unavailable inventory can hide findings. Due dates are review reminders; no email notifications are sent.

Review evidence and notes survive snapshot retention. The link to an expired
source snapshot is removed; the original historical evidence may no longer be
available. Reviews are retained until the environment is deleted. Environment
deletion explicitly removes its review history. Testing/demo and imported
snapshots never establish operational reviews.

## Collection coverage

Choose a 7-, 30- or 90-day window to see available observations, failed
collections, active jobs and observation gaps. Incomplete checks always describe
the latest full snapshot, with its timestamp and a link to the report.

Gaps exceeding twice the **current** sync interval are listed; manual collection
uses 24 hours. Beginning/end gaps are included. Historical schedules are not
recorded, so gaps are evidence limitations rather than claims of scheduler failure.
Pauses and retention can explain missing observations. Paused environments still
show stale/unavailable snapshot evidence.

The dashboard makes collection limitations visible; it is not a percentage of
workload coverage or continuous traffic monitoring. NSX search indexing and RBAC
restrictions still apply even when no failed checks are recorded.

## Upgrade from 0.1.0

Back up PostgreSQL and `.env`, and let active collections finish. Set
`NSX_IMAGE_TAG=0.2.0` in your installation's `.env`, then run:

```sh
docker compose pull
docker compose stop web worker scheduler
docker compose run --rm migrate
docker compose up -d
```

Use your usual Compose file arguments if using the remote database or source
installation. Source installations build the new image first. Migration 0011 adds
review tables and leaves existing snapshot JSON unchanged. Do not delete volumes.

## Observation periods and finding qualification

Use **Administration → Finding review criteria** to configure global defaults or select an
individual environment and save a complete override. **Use global defaults** removes
an override. Only administrators can change these settings; changes are audited.

| Condition | Default observation period |
|---|---:|
| Zero-hit firewall rule | 90 days |
| Empty group | 30 days |
| Unreferenced group or service | 30 days |
| Empty firewall policy | 30 days |
| Disabled firewall rule | 30 days |

Enter the required number of days for each condition and save. The unused-object period applies to both groups and services.

Under **Advanced collection safeguards**, qualification also requires at least three successful observations by default. `0` days means no waiting period: one confirmed observation in the next full collection is sufficient. This replaces the previous meaning of zero (disabled). A value of `1` requires a full 24 hours and the configured minimum observation count; it does not mean one collection. The maximum allowed observation gap defaults to twice the
environment's collection interval (24 hours for manual collection); set a nonzero number of hours to override it.
Ensure retention and collection frequency support the history you need.

**Observing** means evidence is accumulating. **Eligible for review** means the
configured duration and observation count were reached. **Insufficient evidence**
means the condition cannot be established. **Condition cleared** requires positive
opposite evidence (for example members or recorded hits); a missing object alone
never proves resolution. Legacy **Qualification disabled** assessments remain unchanged in historical snapshots. Review state (Open/Acknowledged) remains independent.

Periods restart on relevant evidence/configuration changes, reappearance, policy
changes, or gaps beyond the limit. Unknown and excluded observations break the
period. Failed collections and demo/imported snapshots do not add observations.
Fresh timestamped zero counters are required for zero-hit qualification: repeated
old statistics cannot advance it. A positive unchanged cumulative counter is **not**
classified as zero hits or inactivity. These sampled observations are not continuous
traffic monitoring or proof that an object is safe to delete.

Existing findings start with insufficient evidence; their old first-seen date does
not establish continuity. The next full collection begins the observation period.
No historical snapshot refresh is needed. Policy changes take effect at the next
full collection and restart affected periods. Assessments are shown as of their
last collection; stale results must not be treated as a current safety guarantee.

Inventory and firewall rows and evidence dialogs show the assessment captured with
that snapshot. CSV evidence includes assessments where available. Old snapshots
without assessments stay unqualified, and changing policy does not rewrite history.
Snapshot retention removes its assessment rows but preserves the current finding's
small observation summary and review history. Page loads query only the displayed
rows, never all historical report JSON.

Finding review filters and sorting run in PostgreSQL before pagination. Waiting-period settings are managed separately in Administration.

## Personal notification settings

Open the account menu → **Personal settings → Notifications**, or use **Notification settings** in the notification bell. Enable **Use my notification settings** to override the shared policy for your account. Select failed collections, successful collections, new coverage issues, whether to include testing collections, and a history window of 1–30 days. Save changes to update both the list and unread count.

The suggested personal defaults are failures and new coverage issues enabled, routine successes and testing results disabled, and a 7-day window. Until personal settings are enabled, the existing shared policy applies. Reset to defaults restores shared-policy inheritance. Administrators manage that policy under **Freshness & notifications**. Changing notification preferences does not delete history or mark results read; use **Mark all read** separately.

The list shows at most 50 matching results; the unread count covers all matches within the selected window. Recovery notifications, stale-data notifications and daily finding summaries are not yet generated. Upgrade database migrations through `0032` before using personal notification settings.
