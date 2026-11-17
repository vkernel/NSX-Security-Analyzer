# Claude Code project handover

Updated 13 November 2026. Continue development of the Django NSX Security Analyzer
workspace from the same checkout. The latest application work makes NSX deletion
faster and more reliable (shared live scan per run, in-request dependency order,
paced reads, "Not attempted" for time limits) and hardens collection (publication
time reserve, 429/transport retries, single backoff per burst), followed by
interface clean-up, a task-based in-app user guide, live collection progress
counts and corrected GitHub documentation, then publication before indexing,
warm-start pacing, restructured guides and the coss ui visual language (tokens in
`design-system.css`, component layer in `coss.css`; see docs/ui-patterns.md). That
application build is published;
verification of the user's running deployment is the next operational check if
requested. The remaining review backlog is listed under "Suggested next actions".

## First session

1. Read [project guidance](../CLAUDE.md), [contributor setup](../CONTRIBUTING.md)
   and [architecture](architecture.md). Inspect the current branch and working tree.
2. Review `git log -8 --oneline` and the difference from the application baseline
   below. Documentation commits can be newer than the published runtime image.
3. Read ignored `CLAUDE.local.md`, if present, for this machine's existing isolated
   containers and temporary tools. Keep those local details private.
4. State the current state and the next concrete action. Continue the user's
   new request; the suggestions below are not unfinished implementation tasks.

## Published application baseline

| Item | Value |
| --- | --- |
| Repository | [vkernel/NSX-Security-Analyzer](https://github.com/vkernel/NSX-Security-Analyzer) |
| Branch | `development` |
| Application commit | `937fe7fb4e29849fc4118fb387348b76fa42a37c` |
| Application version | `0.5.3`; inspect the build revision as well as the version |
| Mutable image | `vkernel/nsx-security-analyzer:development` |
| Pinned image | `vkernel/nsx-security-analyzer:development-937fe7f` |
| Published image digest | `sha256:dd756dedd4a95ebc9805e4063829cecb49a2312aa8d95f10727c1a6f64ebf66f` |
| Platforms | `linux/amd64` and `linux/arm64` |
| Latest migration | `0070_collection_slots_and_retry` |

These publication results were verified on 17 November 2026 (build date
`2026-11-17T09:01:41Z`). Recheck mutable tags
and the remote branch before a later publication. The handover documentation is
published separately from the application image. The running cluster's revision
and migration state must be inspected before claiming that a rollout is complete.

## Architecture and code map

The web process reads prepared PostgreSQL snapshot data and handles user actions.
The collector saves inventory and evidence; the scheduler queues scheduled work.
Plain JavaScript and Django templates implement the UI. This is not a React app.
Application work belongs in this checkout; standalone synchronization scripts in a
user's Downloads directory are not the web application's collector.

| Area | Entry points |
| --- | --- |
| Collection and pacing | `webapp/inventory/collector.py`, `request_pacing.py`, `member_collection_budget.py` |
| Direct VM associations | `webapp/inventory/vm_associations.py`, `resolved_membership.py` |
| VM interface attachments | `webapp/inventory/network_membership.py` |
| Saved VM addresses and IP matches | `webapp/inventory/vm_addresses.py`, `ip_references.py` |
| Saved VM and firewall references | `webapp/inventory/vm_relationships.py`, `confirmed_references.py`, `firewall_reference_context.py` |
| Current cached observations | `webapp/inventory/current_membership.py`, `group_membership_paging.py` |
| Group, service and tag references | `webapp/inventory/object_references.py`, `tag_relationships.py` |
| Prepared snapshot browsing | `webapp/inventory/snapshot_index.py`, `report_views.py` |
| Shared evidence drawers | `webapp/static/evidence.js`, `report-data.js`, `application.js`, `workspace-panels.css` |
| Export service and formats | `webapp/inventory/reference_exports.py`, `reference_export_data.py`, `reference_export_formats.py` |
| Export dialog | `webapp/static/reference-exports.js` |
| Naming assessments | `webapp/inventory/naming.py`, `naming_views.py`, `naming_forms.py`, `naming_catalog.py` |
| Finding reviews and My work | `webapp/inventory/history_views.py`, `finding_workflow.py`, `findings.py` |
| Workers and migrations | `webapp/inventory/management/commands/audit_worker.py`, `worker_lanes.py`, `models.py`, `migrations/` |

The supervisor has six independently leased lanes: `collection`, `recalculation`,
`comparison`, `deletion_preview`, `deletion_execution` and `exports`. Their shared
PostgreSQL advisory-lock namespace also contains claim and manager locks: check
all usages before adding a lock ID. Do not reuse a claim lock as a lifetime lease.
Subprocesses still share container CPU, memory and database resources.

## Product decisions to preserve

### Membership and firewall references

The user reported inflated tag-based VM group counts, missing saved memberships,
segment groups with zero direct VM members, missing firewall references through
segment attachments, and missing runtime VM addresses. Treat those as important
regression scenarios, not evidence that the current build has been verified in
production.

Direct VM membership comes from NSX VM/group associations. Segment-based groups
can have zero direct VM members while their effective member ports are attached
to VMs. Attachment links require exact saved VM/VIF/port/segment identities;
matching a segment name or tag is insufficient. Parent-group links must respect
authoritative effective-port evidence rather than inventing parent membership
from arbitrary child definitions.

IP/subnet references use saved runtime VM addresses and supported saved group IP
definitions. They are labelled configuration matches, not confirmed membership,
live address evidence or effective policy. Shared/ambiguous addresses and missing
coverage retain uncertainty. Names and tag values are not address evidence.

Current checks are cached separately. The historical snapshot must remain
unchanged, with its original collection times. Complete-empty direct membership
must not erase a separately verified saved attachment. Retain identity validation,
project/scope separation, pagination checks, partial positives and excluded fields.

### Workspace redesign (published in `development-e7f1f13`)

The accepted redesign is being implemented screen by screen; see
[UI patterns](ui-patterns.md) for each pattern. Done so far: grouped sidebar with
collapsible rail, flyouts, command palette (`api/search/`, saved snapshot names
only, never NSX) and breadcrumb (`nav-shell.js`, `nav-shell.css`); Overview KPI
tiles and finding qualification; Reviews list with a server-rendered side panel
(`reviews_region.html`, `reviews.js`); Inventory overview summary from saved
projections only (`overview_summary.py`); Groups/VMs docking the shared evidence
drawer beside the list on wide screens (`inventory-dock.js`); report views as
chip links; Firewall Rules and Policies in NSX order with a policy panel
(`rule-order.js`, `rule_order.py`, `policy_order.py`); a page per collection
with a result view once completed (`collection_detail.py`); the docked evidence panel has Docked, Wide and Full
screen widths, rule cards when narrow, a resize handle and an Open in new tab link; reference exports let users choose
categories and columns (`reference_export_columns.py`, evidence basis always
included) with per-category CSV (ZIP), Excel, PDF, HTML, JSON and YAML; deletion requests
with a stage title and step cards (presentation only); Services and Tags & scopes
overviews (`catalog-views.js`, `catalog_views.py`); a References page under
Firewall (`object-references.js`); Segmentation application cards and Naming
outcome cards; Get started and Notifications pages; a centered sign-in card. New snapshots record a VM count in `Snapshot.summary['vms']`; older
ones show it as not recorded. `load_demo_data` builds synthetic environments by
running the real collector against an in-process fake NSX on `*.example.invalid`.
All designed screens are built or covered (My work by the Reviews queues,
Preferences by Personal settings); the Rule proposal page shows the proposed rule
and notes. Not built: the Naming name-component side panel and a service
"duplicate of" observation. Group list count columns (direct VMs, interfaces,
IP matches, rules) need collection-time projections and are not built.

### Interface and review workflow

The evidence drawer uses shared table-based references for VMs, groups and
services, including navigation from one referenced object to another. Firewall
reference tables widen the drawer and show Category, Name (with the rule
sequence), ID, Sources, Destinations, Services, Context profiles (only when the
snapshot uses them), Applied to, Action, State, Matched in and Reason / limits.
Rows follow configured NSX order before paging: category (Ethernet, Emergency,
Infrastructure, Environment, Application, then unknown), policy sequence, rule
sequence. Category and policy heading rows carry totals across all pages and
repeat as "continued" on later pages. Missing sequences sort last and show
"Order not recorded". This is configured order, not an effective-policy verdict.
Matched objects are marked in place; negated fields show NOT; a policy-level
Applied To is labelled "From policy". A policy's Applied To selects every rule in
that policy: VM and group references include those rules (read from saved rule
configuration, so existing snapshots benefit) with "Applied to (policy)" in
Matched in. Tag references gain them from new collections only, because tag
firewall edges are prepared at collection time. Tag firewall tables show one row
per rule; a rule reached through several tag-using groups lists every group and
both tag uses (condition and/or group assignment). Exports use the same order and start
with Rule category, adding Policy sequence and Rule sequence columns. Avoid restoring nested collapsible
lists, character-by-character path wrapping or large repeated notices.

VM References contains groups, saved network links, firewall rules and IP/subnet
matches. Assigned tags are metadata; do not reintroduce tag candidates as confirmed
VM memberships or tag-based firewall rules. Collection messages should say
"collection", not "audit", in the product UI even where internal class names retain
legacy terminology.

Finding reviews are the shared review workflow; My work is a scoped work queue.
Keep consistent actions and links rather than creating two competing review
systems. See [the review workflow](finding-review-workflow.md),
[history and reviews](history-and-review.md), [naming policies](naming-policies.md)
and [the UX implementation record](design/production-ux-implementation-2026-11-03.md).
That dated UX record contains earlier states; current code and tests take priority
when later changes supersede an older description.

### Reference exports

The latest feature supports CSV, PDF, JSON, YAML and standalone HTML for VMs,
groups, custom services, tags and scopes. It includes all retained pages in the
selected category or all categories. Current direct observations, when selected,
are frozen from the local cache at request time. No export may initiate NSX calls.

Preserve per-category coverage, source labels and timestamps; an empty export is
not proof of complete collection or non-use. Downloads belong to the requesting
user, expire after 24 hours and have explicit size, queue and storage limits.
Artifacts are compressed in PostgreSQL. The export lane has a five-minute process
deadline and its own heartbeat. Read [reference exports](reference-exports.md)
before changing format schemas, job lifecycle or deployment requirements.

### Collection reliability (published in `development-2e470ec`)

Migration `0070` adds `AuditJob.not_before`, `retry_of` and `worker_slot`.
Collection lanes `collection`, `collection_2`…`collection_4` each hold their own
advisory lock (IDs 781249311 and 781249331–333); `NSX_COLLECTION_SLOTS` (default 1)
chooses how many run. A lane that takes its lock fails collections still running
for its slot (`WORKER_ORPHANED`). `services.failure_code` classifies failures by
exception type and HTTP status; `schedule_retry` queues one delayed retry for
temporary codes only. Reference-search gaps publish with `search_coverage.failed`
and unknown usage instead of failing the collection; unused findings pause.
The network phase starts once search and membership finish (`OVERLAP_NETWORK_PHASE`)
and must reuse the same VM records saved in `tags.virtual_machines`, because it
adds interface details to them. Two bulk counter probes run only with eight or more
counter workers. `summary.performance` feeds the staff-only performance trend.
Tests: `inventory.test_collection_resilience` and `test_collector.py`.

### Firewall Summary (published in `development-457a760`)

`collector.dfw_figures` computes the Firewall Summary figures (version 1). Publication
saves them in `summary.firewall`; `firewall_overview.py` reads that key, or for older
snapshots computes the same figures once from the saved `dfw` index rows and the
`summary.evidence_coverage` key (cached per process). The snapshot page must never
read report JSON (`test_snapshot_index` enforces this). `firewall_overview_summary.html` is a `<template>` that
`application.js` swaps into `#dfw-overview`, replacing the legacy report HTML (still
generated by `collector.dfw_overview` for the stored report). The segmentation figure
stays a rule configuration proxy: Not assessed for testing or incomplete evidence,
N/A without enabled ALLOW rules. Tests: `inventory.test_firewall_overview`.

### Drawer design (published in `development-937fe7f`)

`application.js` builds every evidence drawer's Summary in one place
(`evidenceSummary`, `ruleCards`, `summaryCards`): state badges, the per-kind
membership strip (unknown shows a dash, never zero), count tiles and cards.
Rule fields mark excluded Source/Destination with NOT and a policy Applied To
with From policy. Drawer styles live at the end of `content.css`, scoped to
`#detail-body` and `.rule-evidence-drawer`. The segmentation scope check and
drawer share `workload_rules.effective_scope` (policy Applied To other than ANY
takes precedence). Tests: `tests-js/evidence-summary.test.cjs` and
`inventory.test_workload_rules`.

## Verification baseline

The published application baseline (`937fe7f`) passed 1,095 Django tests and
142 JavaScript tests on 17 November 2026; the offline collector tests (113, 9
skipped) last ran on `457a760` and the collector is unchanged since. Both registry platforms ran by digest against separate empty databases:
non-root user, build revision and date, fresh migrations to `0070`, system check
and static assets passed. The collection-slot work (`2e470ec`) also passed a
live PostgreSQL check with two slots: a worker killed with SIGKILL mid-collection
left the job running, and restarting the lane failed it as `WORKER_ORPHANED` with
exactly one queued retry. `test_workload_rules` failed once in
an earlier full run and passed alone and in the next full run; treat it as
order-sensitive until investigated. The
table below records the earlier 13 November baseline checks.

| Check | Result |
| --- | --- |
| Django inventory tests on isolated PostgreSQL | 1,025 passed |
| JavaScript tests | 114 passed |
| Offline collector tests | 113 run; successful result |
| Browser export acceptance | 1,001 synthetic firewall references, all five formats, no export-triggered NSX requests (11 November); 13 November: synthetic VM/group/tag/custom-service exports through the worker lane in NSX order |
| UI and PDF review | Light/dark, mobile, keyboard focus/return, 44-page landscape PDF with final rule present; 13 November: firewall drawers (VM, group, tag, custom service) and PDF header wrapping |
| Image checks on both CPU architectures | Registry images pulled by digest: non-root execution, build revision, fresh migrations, assets, export lane/heartbeat and all renderers passed |

Use [the committed synthetic acceptance record](performance/reference-exports-2026-11-11.json)
for browser measurements. Its earlier full-suite count predates the final
publication rerun of 987 tests. These are synthetic checks, not production
collection-time guarantees. No production acceptance check is claimed.

After configuring a disposable PostgreSQL test environment as described in
[Contributing](../CONTRIBUTING.md), run from the repository root:

```sh
python -m unittest discover -s . -p test_collector.py
python webapp/manage.py test inventory --noinput
python webapp/manage.py check
python webapp/manage.py makemigrations --check --dry-run
python webapp/manage.py collectstatic --noinput
```

Run the separate Node suite using the instructions in
[JavaScript validation](../CONTRIBUTING.md#javascript-validation). Django/JSONB and
concurrency behavior requires PostgreSQL; a SQLite smoke test is not a substitute.
Useful focused tests include `test_vm_associations`, `test_network_membership`,
`test_resolved_membership`, `test_vm_addresses`, `test_ip_references`,
`test_vm_relationships`, `test_naming_consistency`, `test_reference_exports` and
`test_worker_lanes`, all under `inventory`.

For UI changes, exercise a different object's drawer and Back navigation, not only
an isolated renderer. Use synthetic fixtures; do not request real VM names,
snapshots or credentials to establish correctness.

## Deployment and publication

Use [operations](operations.md), [Docker installation](docker-hub.md) and
[Kubernetes installation](../deploy/kubernetes/README.md). Apply migrations with
matching web, worker, scheduler and migration-job images. Release installation
manifests can still default to `0.5.3`; explicitly select a development tag when
validating development features.

Migration `0069` creates the export job table. An installation that starts explicit
worker lanes must add `exports`; the default supervisor already includes it.
Check its heartbeat after upgrading. Existing snapshots can be exported without
collecting again, subject to the evidence actually retained in those snapshots.

Migration `0063` builds projection indexes concurrently and includes progress
logging/recovery checks. Prior builds encountered existing index relations and
long WAL/index work. The native Kubernetes migration Job currently allows
`activeDeadlineSeconds: 3600`. Inspect database progress and blocking before
changing deadlines, cancelling index work or faking a migration. A previous
successful upgrade does not establish every later installation's migration state.

For another application publication, use a clean committed checkout, full
`APP_REVISION`, UTC `APP_BUILD_DATE` and a unique development image tag. Test the
actual built images, inspect both registry platforms, then update `development`.
Do not overwrite stable release tags or imply that publishing deployed a cluster.
This handover updates GitHub documentation and does not rebuild the runtime image.

## Suggested next actions

1. If the user wants deployment acceptance, verify the selected image revision on
   every component, migration `0069`, worker heartbeat and an export from an
   existing snapshot. Confirm with a read-only NSX policy read that security
   policies return `scope` (Applied To) and how whole-DFW is represented. Prefer read-only checks; ask for missing access only when
   necessary, and use synthetic data for reproduction.
2. Keep a synthetic regression case for every membership/address/reference issue.
   Measure request counts and phase times before optimizing collection. Preserve
   coverage and identity checks when reducing API work.
3. Extend synthetic export tests for unusually long names, dense provenance,
   incomplete evidence, missing cache observations and large files before raising
   limits. Preserve the existing owner, expiry and format contracts.
4. Keep shared drawer/table components consistent across object navigation. Treat
   obsolete mockups and dated screenshots as historical design references.
5. Add the separate JavaScript suite to GitHub Actions as a scoped follow-up task.
   The current workflow installs Node for collector checks but does not run
   `webapp/tests-js/*.test.cjs`; those 114 checks currently need a separate run.
6. Open review backlog from 13 November 2026. P2 items done on development: UI
   caveats and "audit" wording, Reviews terminology, task-based in-app help
   (`feature_guide()` in `collector.py`), GitHub doc corrections and live collection
   progress counts. Also done: restructured `finding-review-workflow.md` and
   `collection-performance.md`; browsing index built after publication (status
   `indexing`, background rebuild on failure); warm-start pacing; parallel VIF read;
   chart colour tokens; more pages on the shared page header. Still open: measure
   real phase times before moving the whole network phase earlier; unify the two
   reference passes in collection; the remaining hand-written page headings and
   empty states. The standalone report shell in `collector.py` is not dead code: the
   workspace splits it into fragments. Earlier P3 notes for reference — warm-start pacing, collection CPU clean-ups, shared page header/empty
   state components, theme tokens for chart colours and removal of the dead
   standalone report code.
7. Update this baseline when significant code or publication state changes. Track
   new work as separate scoped tasks rather than assuming old chat proposals are
   still outstanding.

## Starting prompt for Claude Code

```text
Read CLAUDE.md and docs/claude-code-handover.md, then CLAUDE.local.md if it exists.
Check the current branch and working tree, and identify changes since application
build 5bf667c. Preserve unrelated edits and use synthetic data for verification.
Summarize the current implementation and proposed next steps, then continue with
my next task. Do not publish an image or change a deployment unless I request it.
```

The root entry point follows [Claude Code's project memory guidance](https://code.claude.com/docs/en/memory).
The project's shared guidance stays short; machine-specific context belongs in
ignored `CLAUDE.local.md` rather than a copied chat transcript or credential files.
