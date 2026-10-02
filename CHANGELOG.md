# Changelog

## Unreleased — development

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

Upgrade: apply migration `0021`, then deploy the updated web, worker and scheduler images. Older workers do not process stop requests. See [snapshot maintenance](docs/snapshot-maintenance.md).

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
