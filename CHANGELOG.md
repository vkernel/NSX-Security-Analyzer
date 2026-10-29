# Changelog

## Development — 2026-10-29

- Add read-only VM naming policies with ordered components, environment scope mappings, category rules, draft impact previews, activation/version history, compliance and collision checks, bulk proposal ownership, CSV onboarding, stale-proposal safeguards and audit events. Apply migration 0055.

- Fix new deletion previews looping back to expired requests: retire expired, unconfirmed verification entries under lock and require fresh checks. Expired entries no longer claim they are ready to confirm.

- Apply the Network Shield SVG identity to sign-in, navigation, favicon, error/status pages, report guide, standalone reports and README; provide light/dark documentation assets.

- Synchronize approval controls with server readiness after live verification, retaining draft reasons and blocking stale revisions.
- Keep task status updates automatic with reconnect backoff, retry-only controls, stable list updates, and preserved reading/editing state.
- Render review Actions in a viewport-positioned popover and remove the sticky decision card scrollbar.

## Development — 2026-10-28

- Add Evidence for approval for groups, services, firewall rules and policies: searchable saved details, approval comparisons, and read-only current-state checks with progress, member lists, explicit failures and renewed-review safeguards. Apply migration 0054 and deploy matching web/worker images.

- Fix evidence-button column sizing in group and service inventory tables with bulk selection, including mobile checkbox spacing.
- Preserve verified finding observations across skipped coverage warnings during history replay, subject to the effective maximum gap. Separate historical findings from current eligibility totals. Save unchanged review criteria after deployment to recover periods from retained evidence.
- Deduplicate large snapshot search and relationship payloads, add bounded compaction commands and reliable per-environment retention scheduling, and expose storage growth and configurable budget alerts.
- Add optional browsing-index archival with background restoration, bounded cache cleanup, optional legacy HTML removal, and Docker log rotation. Apply migrations 0051–0053 before starting the updated application components; historical compaction and browsing archival are opt-in.

## Development — 2026-10-26

- Add an independent confirmed-deletion lane. Commit verified deletion evidence with finding closure and prevent overlapping snapshots/history replay from restoring deleted identities. Preserve review for newly recreated identities.

- Try flat NOT and ! runtime exclusions within the existing search budget. Record failed pagination totals and duplicate/unique counts by resource type in live deletion diagnostics.

- Ignore excluded runtime-record payload conflicts only during configuration dependency searches while retaining identity/count/cursor verification. Clarify inventory verification errors and contain deletion diagnostics within the results layout.

- Add unfiltered broad configuration-search fallbacks with runtime filtering after verified pagination. Preserve inventory checks and limited fallback restrictions.
- Save and show live dependency-search diagnostics on deletion previews, including blocked results.

## Development — 2026-10-23

- Support flat VM Name/Tag AND conditions for direct deletion verification. Close and cross-link related findings only after verified object absence, preserve approvals, and restart review for recreated object identities.

- Run read-only deletion previews in an independent supervised lane in the existing worker container. Keep confirmed deletion serialized with collection; add queue labels and preview heartbeat monitoring.

- Deletion previews now explain why direct group verification is unavailable,
  including collection-specific evidence failures and observation gap limits,
  instead of showing only the generic VM inventory warning.

- Add an execution-only direct membership path for already-approved simple VM groups when only global VM pagination is incomplete. Retain fresh qualified history and require explicit zero VM/IP/port checks, unchanged evidence and full live dependency verification.

- Scope inventory-verification pauses to supported evidence dependencies; retain decisions during affected inventory uncertainty and keep unclassified failures blocking.
- Apply the same coverage scopes to collection publication, historical recalculation and approval readiness. Preserve observation thresholds and normal unknown-evidence handling.
- Add selected-row Actions for deletion previews and release; show coverage blockers and per-object verification results. Explicit administrator confirmation remains separate from finding approvals.
- Add migration `0047_deletion_verification`; per-object previews expire after ten minutes and legacy previews must be recreated.

- Add administrator-only saved deletion configuration dialogs with copy/download JSON, group details and manual recreation guidance. History is read-only; no recovery API is implemented.

- Add category-specific single/four-eyes approval settings, a read-only settings preview and targeted approval resets.
- Clarify limited dependency-search verification for groups and custom services independently of approval count.
- Add administrator-only single/bulk NSX deletion previews, explicit ticketed confirmation, live revalidation and per-object durable outcomes. Deletion shares the collection lane; no automatic DELETE retry or rollback.
- Add migrations `0045_category_approval_modes` and `0046_approved_nsx_deletion`; deploy migrations and matching web/worker images together.
- Update Help & coverage, review instructions and operations guidance. Existing screenshots are labelled as predating the new controls.

## 0.5.3 — 2026-10-13

- Add compact finding Actions dialogs and contextual bulk return to unassigned, preserving permissions, revision checks, review history and valid approvals.
- Add a compact comparison toolbar and two-snapshot selection shortcut; simplify audit filters, diagnostic collection and approval-setting explanations.
- Run collection, finding recalculation and comparison in independent lanes; correct the comparison lease collision with finding task claiming.
- Retry empty broad configuration searches against alternative syntax and explicit resource types while retaining coverage checks and limited-scope warnings.
- Tolerate seven known VM runtime-field changes on the dedicated VM endpoint; retain identity, tag and completeness checks. Persistent inventory verification warnings pause finding evaluation and approval.
- Refresh documentation and Help & coverage for current review controls, worker queues, deployment identity and collection verification. Historical release entries below describe their original behavior.


- Use “collection worker” throughout user-facing queue status; enforce one global collection and one independently running finding-history recalculation and one snapshot comparison with PostgreSQL worker-lane leases.

- Fix finding-history replay to restore current presence and evidence; separate condition continuity from review fingerprints and upgrade cached historical evidence during replay.
- Use a 24-hour minimum automatic finding-observation gap, preserve explicit overrides, and explain withheld findings in the review queue.
- Update in-app Help & coverage with finding criteria, historical recalculation, workspace controls, authentication settings and collection pacing guidance, including for existing indexed snapshots.

- Refresh the evidence drawer with Overview, Relationships and Technical details tabs, a wider desktop view, and on-demand VM relationship sections.
- Simplify finding filters, expose inventory/firewall sorting, improve comparison and finding-evidence layouts, and group administration settings.
- Highlight actionable collection failures and stale environments; clarify coverage and stop-request presentation without changing evidence classifications.
- Exclude shared pacing and concurrency waits from individual request retry budgets, apply HTTP 429 cooldowns once, and abort when the overall collection deadline expires.

- Allow stable collection traffic to grow from 10 to 40 requests/second, prioritize likely membership endpoints without reusing stale results, and log measured request and group progress.

- Pace collection requests, share throttling cooldowns, honor Retry-After with bounded randomized retries, and serialize collections for duplicate manager origins.

- Enable retention by default for new installations: 180-day full snapshots and collection history, 7-day testing snapshots. Preserve existing saved policies.

- Load VM relationship sections in bounded pages, fetch rule details on expansion, cache recent responses and deduplicate rules while preserving group provenance.

- Remove the optional refresh Job manifest to prevent suspended maintenance from blocking Argo CD; document explicit temporary maintenance Jobs instead.

- Local user administration now offers the same Viewer, Operator and Administrator roles as Keycloak, with a shared permission mapping.

- Optional Keycloak sign-in with PKCE, verified identity tokens, explicit access roles and local login fallback.

- Prepare compact coverage summaries and paginated issue rows during collection (migration 0024), removing report JSON reads and inventory scans from coverage browsing. Remove unnecessary snapshot joins/summary extraction from polling and collection lists.

- Load environment analysis tabs on demand: shared paginated comparisons, read-only finding pages, deferred finding evidence and SQL-paginated coverage issues. Add migrations 0022/0023 for comparison storage and history indexes.

- Suspend the optional Kubernetes snapshot-refresh Job by default; require an explicit manual start and document collection maintenance windows.

- Add an optional `snapshot-refresh` Compose container and standalone Kubernetes maintenance Job, excluded from normal startup and Argo CD sync.
- Add staff-only collection stop controls with persistent requests, worker process termination, rollback of unfinished snapshots and audit events.
- Add small/medium/large resource sizing and maintenance instructions.

Upgrade from 0.5.2 or development: apply all pending migrations from the selected image (currently through `0040`), then deploy matching web, worker and scheduler images. Stop old worker replicas before switching lane implementations. Older workers do not process all current tasks and stop requests. See [snapshot maintenance](docs/snapshot-maintenance.md).

## 0.5.2 — 2026-10-01

- Add Inventory → VMs with paginated VM identities, power states, tags and on-demand group, rule and service relationships.
- Reuse the existing VM inventory request, including untagged VMs; label incomplete and legacy coverage. Relationships do not assert resolved membership or traffic.
- Batch environment listings and reduce payloads on workspace, collection, notification, coverage and finding pages.
- Reduce snapshot comparison selector and configuration payloads.

Upgrade: keep the existing database and Django secret, run the standard migration job, update all application services, then run `python manage.py index_snapshots --refresh`. This refreshes prepared report layouts without changing original snapshots. Run a new collection to include previously unsaved VM details and untagged VMs. No new schema migration is required beyond 0.5.1.

## 0.5.1 — 2026-10-01

- Reduce Inventory and Firewall first-load metadata queries.
- Reuse historical activity assessments with database pagination and invalidation when collection history changes.
- Load only relevant tag evidence; debounce filter suggestions and reuse signed matching-row counts.
- Add compact history projections and migrations 0019/0020.
- Run Kubernetes migrations as an ordered Argo CD Sync hook to avoid immutable Job update errors.

Upgrade: apply migrations and restart application services, then run `python manage.py index_snapshots --refresh` once to update existing prepared reports. Original snapshots are preserved.

## 0.5.0 — 2026-09-30

- Load inventory and firewall summaries without reading or rendering the full snapshot on each first visit.
- Prepare queryable snapshot records in PostgreSQL; load sections and evidence on demand with server-side pagination, search and sorting.
- Export all matching records to CSV, including evidence, independently of the visible page.
- Add migration `0018_snapshot_presentation` and the restartable `index_snapshots` command for existing snapshots. Original JSONB reports remain intact.
- Keep Inventory and Firewall navigation within the selected snapshot.
- Document separate Kubernetes Django and PostgreSQL secrets, including mapping an existing secret’s `password` key.

Upgrade: back up PostgreSQL, preserve `DJANGO_SECRET_KEY`, run migrations, restart application services, then run `python manage.py index_snapshots`. New collections prepare their indexes automatically.

## 0.4.0 — 2026-09-30

- Add structured container logging, collection heartbeats, sanitized failure diagnostics and persistent administration audit events.
- Preserve masked draft passwords during Manager certificate retrieval, including failed attempts.

- Add an ordered Kubernetes deployment guide and native manifests, correct converter image references, and simplify Docker and Kubernetes installation instructions.

- Provision the initial admin account once during database initialization, preserving existing accounts and changed passwords.

- Remove experimental traffic receiver, configuration wizard and exporter discovery from the application. Traffic analysis is deferred.
- Keep NSX Manager certificate retrieval independent of the removed feature.
- Preserve historical migrations and archived setup records without exposing them in the application.


## 0.3.1

- Create a private home directory owned by the non-root application user so Gunicorn can initialize its control socket without a permission error.


## 0.3.0

- Expand Docker Compose service definitions and add a standalone, credential-free Kubernetes conversion template with a deployment checklist.


- Add bounded, expiring IPFIX source diagnostics, separate UDP intake and queue/socket-drop counters.
- Add read-only export-readiness evidence and refresh to the setup review.
- Add an isolated, digest-pinned GoFlow2 synthetic compatibility harness; real NSX decoding remains unverified.


- Add explicit existing/new IPFIX profile selection, named profiles, priority review and optional group activation for existing profiles.

- Add an explicitly approved IPFIX setup wizard with revision checks, preserved collector destinations, persistent outcomes and delivery verification.

- Replace setup certificate uploads with reviewed retrieval, add persistent environment certificate trust, and align certificate forms with application styling.

- Retrieve vCenter CA bundles during discovery setup with explicit fingerprint review and scoped trust.

- Discover ESXi management IPv4 exporters through a verified, read-only vCenter connection; preview and select mappings without storing credentials.

- Start the IPFIX proof of concept with optional Docker UDP reception.
- Add staff-only exporter/environment mapping, heartbeat and bounded template previews.
- Add migration 0012 for metadata; no raw traffic or decoded flows are retained.

## 0.2.2-dev

- Overlap search and DFW collection while retaining index completeness checks.
- Serialize optional bulk probes and isolate their read timeouts from global backpressure.

- Mark Layer-2 rule statistics as unsupported and skip their requests.
- Retry timed-out rule statistics once in a delayed sequential recovery pass.

- Reuse HTTPS connections with uploaded-CA validation and redirect protection.
- Persist increasing policy-statistics cooldowns, probe for recovery, and always retrieve fresh rule counters.
- Start rule fallbacks as policies finish, within the shared adaptive request limit.
- Bound optional bulk request timeouts independently of rule collection.
- Save endpoint failures and latency diagnostics and expose them in snapshot details.

## 0.2.1

- Restart paginated NSX search up to twice when returned totals are inconsistent.
- Discard incomplete attempts and preserve the previous snapshot if retries fail.
- Explain inventory changes and search indexing delays in collection error guidance.

## 0.2.0

- Add snapshot comparison for rules, groups, services and membership evidence.
- Add persistent finding ownership, acknowledgement, notes and review dates.
- Reopen findings when relevant evidence changes; retain notes across snapshot retention.
- Add collection coverage, incomplete checks, stale evidence and observation gap views.
- Save fuller NSX configuration without additional API requests.
- Add migration 0011 for finding review tables; existing snapshots remain unchanged.

## 0.1.0

- Make the web GUI the only product interface; remove standalone CLI, file reports and manager-file import.
- Move collection into the application package; require saved GUI credentials for collection.

- Adopt Apache License 2.0 with project attribution and container license metadata.

Initial standalone repository for NSX Security Analyzer.

- Read-only NSX Policy collection and database-backed report viewing.
- Multi-environment web workspace with PostgreSQL snapshots and scheduled collection.
- Inventory, firewall activity history, reference evidence and coverage review.
- Retention policies, user preferences, progress indicators and readable errors.
- Synthetic demo environments and searchable environment/snapshot navigation.
- Product documentation, contribution guidance and continuous integration.
