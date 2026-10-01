# Collection performance

Development builds from 0.2.2-dev retain fresh inventory and counter checks while reducing transport and fallback overhead.

- HTTPS connections are pooled per collection (up to 16 connections); the existing adaptive limiter still caps concurrent requests at eight. Uploaded CA certificates and hostname validation remain supported. Redirects are rejected, and the pool closes when collection finishes. System HTTPS proxy settings and proxy bypass rules are honored.
- Optional policy statistics use an eight-second connect/read timeout, or the environment timeout if shorter. Each bulk request has one attempt. Individual rule requests retain the environment timeout and retry policy.
- Failed policy endpoints use a cooldown saved in PostgreSQL snapshot data. Transient failures start at one hour; HTTP 400/404/405/501 start at six hours. Repeated failures double the delay up to 24 hours. After expiry, collection probes the endpoint again. A successful complete response clears its failure history. Manager mismatches and testing snapshots are ignored. These are endpoint failure records, never cached counters.
- Rule fallback requests become eligible as soon as their policy request finishes; they do not wait for all bulk requests. Both use one bounded executor and the existing shared adaptive request limiter.
- Incomplete, ambiguous, or malformed bulk evidence still falls back to individual rules. Search scope, pagination checks, membership checks and enforcement-point coverage are unchanged.

## Diagnostics

New snapshots save DFW strategy counts in `dfw.collection_diagnostics`: policies evaluated, fully successful bulk policies, policies skipped during cooldown, fallback rules and categorized reasons (counted per rule). Empty policies are excluded.

`performance.concurrency.endpoints` includes successful and failed attempts, failure counts by HTTP status, total request time, maximum latency, and p95 latency from the most recent 512 attempts per endpoint family. Timing excludes waiting for the adaptive limiter but includes transport time. Request durations can overlap; their sum is not collection elapsed time. Existing phase timings remain the wall-clock reference.

Cooldown history comes from the latest successful non-testing snapshot. If collection fails before saving a snapshot, new cooldown information from that run is not persisted. Retention or deleting snapshots may also remove failure history; the next collection then probes normally.

## Validation

Compare several collections of equivalent inventories using total elapsed time, DFW phase time, bulk success/skip counts, fallback counts and unknown statistics. A faster run is not an improvement if coverage deteriorates. Offline regression tests check fallback equivalence, early fallback scheduling, increasing cooldown and recovery, bounded diagnostics, redirects, uploaded-CA validation and actual TLS connection reuse.

References: [NSX policy statistics](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_GetSecurityPolicyStatistics.html), [urllib3 connection pooling and TLS](https://urllib3.readthedocs.io/en/stable/advanced-usage.html).

## Unsupported statistics and timeout recovery

Ethernet policy rules are retained in inventory with **Not supported** activity and no hit count. Their statistics endpoints are not queried. An explicit NSX HTTP 400 / error 500209 response is also recognized as unsupported. This does not establish zero traffic. Unsupported rules remain in inventory totals, are shown separately from unknown statistics, and cannot qualify as historical zero-activity evidence.

After the first statistics pass completes, transport timeouts get one recovery pass, one rule at a time, following a two-second delay. HTTP errors and malformed results do not trigger this recovery. The recovery uses the existing request timeout and HTTP retry policy. Initial timeout notes and timestamps are retained in `statistics_retry`; a failed recovery stays unknown. Diagnostics count timeout retries, recoveries, and unsupported rules. A collection can take longer when recovering missing evidence.

Statistics tasks are capped at two concurrent workers in addition to the shared adaptive request limiter. Inventory concurrency is unchanged. Sequential recovery runs after that pool completes. Compare one versus two using equivalent read-only rule requests before raising this cap; a short comparison does not establish capacity for every manager.

## Overlapping phases and optional probes

Search and DFW collection run concurrently after initial inventory, through the same adaptive request limiter. Testing and single-worker calls stay sequential. Search completeness failures still discard the audit; no partial snapshot is accepted. Individual `search` and `dfw` phase times overlap and must not be added together. `search_and_dfw` records their combined wall time.

Only one optional policy-statistics task is scheduled at a time. Remaining statistics capacity can serve rule fallbacks. A bulk read timeout is recorded and enters cooldown without reducing the manager-wide concurrency limit. Rule timeouts, connection failures, and explicit HTTP backpressure (including 429/503) still reduce the limit. No NSX-side configuration or service restart is performed.

## Report navigation

On an open snapshot, Inventory and Firewall switch sections without fetching the
report again. The selected snapshot stays fixed while navigating its sections; use
the snapshot selector or reopen Inventory from another page to load a newer snapshot.

New snapshots prepare their report presentation during collection, in the same
transaction as snapshot publication. PostgreSQL retains the original JSONB report
and stores separate summary panels, table records, and evidence. This uses additional
database storage and adds an `index_snapshot` phase to collection diagnostics, but
removes full report parsing and rendering from the first page request.

The first response contains the inventory and firewall summaries. Other sections
load when selected. Tables retrieve 25, 50, or 100 records at a time; search, column
filters, and sorting run across the selected section in PostgreSQL. Evidence is
requested only when its dialog opens. CSV exports stream **all matching records**
in the current sort order, including evidence as JSON in the final column. A CSV
export is not limited to the visible page.

Plain-text search supports AND / OR and quoted phrases. Indexed reports use
PostgreSQL regular expressions, whose syntax can differ from JavaScript regexes in
older reports. Search and export database statements have a 15-second limit; a very
complex expression may need to be simplified. Column value suggestions return at
most 100 distinct values. Name sorting is case-insensitive text ordering.

All report endpoints require authentication, check snapshot existence, and return
private, non-cacheable responses. The queryable presentation is shared across web
pods through PostgreSQL; no Redis or per-pod warm-up is required. Snapshot retention
also deletes these associated records. NSX is never queried while browsing a report.

### Existing snapshots

After upgrading to a build that includes migration `0018_snapshot_presentation`,
run migrations through the normal deployment process, then prepare older snapshots:

```sh
# Docker Compose: run from the folder containing compose.yaml and .env.
docker compose exec web python manage.py index_snapshots

# Kubernetes: use the namespace and web Deployment from your installation.
kubectl -n nsx-security-analyzer exec deployment/web -- python manage.py index_snapshots
```

The command reads one saved snapshot at a time, commits each index atomically, and
skips snapshots already indexed. It can be restarted after interruption. Allow it
to finish before measuring cold-page performance for older snapshots. Until indexed,
older snapshots remain readable through the previous renderer and its bounded
per-process cache. Newly collected snapshots are indexed automatically.

### Diagnosing a slow first page

The report page reads only display metadata for its snapshot selector (IDs,
timestamps and testing flags). It does not fetch historical summary JSON, report
JSON, or environment credentials for that selector. Migration
`0019_snapshot_latest_index` adds an index for selecting an environment's newest
snapshots in date order.

Web container logs include a `Snapshot page` entry with `mode=indexed` or
`mode=legacy`, timings for metadata retrieval, presentation loading and template
rendering, and the response size. Inspect them with:

```sh
kubectl -n nsx-security-analyzer logs deployment/web --since=10m
```

`mode=legacy` means the snapshot has not been indexed and still needs the old
full-report renderer. Run the `index_snapshots` command above; also ensure the
collection worker runs the updated image so new snapshots are indexed. The report
page displays a notice for unindexed snapshots.

For indexed reports, a long metadata time suggests database query/connection
latency; a long presentation time points to retrieving the prepared presentation.
Compare the request timing with the browser's network timing to distinguish server
work from ingress delays, network transfer and browser rendering. The logs alone
do not measure those browser or ingress delays.

### Inventory and Firewall subpages

Table page changes reuse a signed matching-row count for five minutes. Changing the
search or column filters triggers a new count. Evidence remains available on demand,
and CSV exports still include every matching row. Column value searches wait briefly
while typing and cancel superseded requests. Evidence columns accept text filters
without aggregating large evidence payloads into suggestion lists.

Tag details select only the referenced conditions and firewall rules from PostgreSQL;
tag coverage selects only its relevant conditions.

Historical activity saves a shared assessment in PostgreSQL and filters and paginates
those saved rows. The first request for a time window still calculates the assessment.
Subsequent requests reuse it for up to five minutes, with the assessed period shown on
the page. Snapshot and collection-result changes invalidate it. Compact counter
projections reduce the data read during assessment; the original snapshots remain intact.

After deploying this update and applying migrations, refresh existing prepared reports
once to update their embedded controls and add the compact history projections:

```sh
docker compose exec web python manage.py index_snapshots --refresh
```

In Kubernetes, run the same management command in the web pod:

```sh
kubectl -n nsx-security-analyzer exec deployment/web -- python manage.py index_snapshots --refresh
```

Use your actual web Deployment name if different. This processes saved data without
contacting NSX. New snapshots receive the updated indexes automatically. Until old
snapshots are refreshed, historical analysis can still read their original DFW data.


### Environment and workspace pages

Environment directories and the workspace overview batch latest-snapshot and active-job
lookups. Freshness timestamps are selected in the environment query, so adding rows
does not add a separate set of queries per environment. Snapshot lists select only the
small summary values displayed on the page, rather than transferring coverage-key
arrays with every row. Collections, progress polling and notifications exclude report
bodies, job configuration and unused diagnostic payloads.

Prepared snapshot coverage reads unknown records and the required report metadata;
legacy unindexed snapshots retain their original fallback. Findings pages check whether
reviews are already synchronized before fetching report evidence, and defer evidence
on list rows. Comparison selectors load only snapshot IDs and timestamps. Comparing
two snapshots still requires examining both configurations, but unrelated report sections
are excluded. The first findings synchronization and legacy coverage can still be costly
on large snapshots; ordinary environment navigation does not perform these analyses.

These query changes need no additional migration beyond the existing release migrations.
They require updating/restarting the web application image. Regression tests check the
absence of large payload fields on listing routes, constant directory query counts, and
coverage equivalence with the original report.

### VM inventory

Inventory → VMs lists saved VM names and identities, power states, assigned tag counts
and groups referencing those tags. The details panel shows scoped tags, related groups,
firewall rules (including disabled rules), configured services, and the VM fields NSX
returned, such as external/compute identities and host or guest information where available.

The collector reuses the VM list already retrieved for tag analysis; no per-VM API calls
are added. Table rows are indexed in PostgreSQL with pagination, search, sorting, CSV
export and on-demand evidence. The complete VM list is excluded from shared tag metadata.

Relations are configuration evidence, not resolved VM membership or effective policy.
Group metadata tag assignments are excluded from VM-to-rule relationships. Other grouping
methods, including static membership and IP criteria, are not resolved by this view.
Services are those configured on related rules; they are not observed VM traffic.

After deploying, run `python manage.py index_snapshots --refresh` for existing reports to
receive the new tab and renderer. Older snapshots can show only VMs recorded in their
tag assignments and display a limitation notice. A new collection saves the complete
returned VM inventory, including untagged VMs. Incomplete or sampled inventory is labelled.

VM fields follow the [Broadcom NSX VM inventory API](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_ListAllVirtualMachines.html);
fields absent from NSX are not inferred.


### Refresh memory usage

Index construction now receives structured row references directly from report preparation.
It no longer embeds the entire inventory in HTML and JSON-decodes that inventory again.
Only the layout is rendered; expanded database records are inserted in batches of at most
50, and panel links in batches of 1,000. Refresh logs the current snapshot, inserted record
counts and elapsed time. Each snapshot remains one transaction, including replacement of
its old index; a failed refresh rolls back that snapshot's index.

Run large refreshes in a separate maintenance Job, not inside the live web container.
The snapshot JSON and derived relationship data still occupy memory, so this is not a
fixed-memory streaming reader and no universal memory limit can be guaranteed. Use the
updated application build in the maintenance Job: version 0.5.2 does not contain this fix.

Refresh also deletes the old derived index directly in PostgreSQL before rebuilding,
inside the same snapshot transaction. Ordinary ORM cascade deletion can materialize
all old evidence records when delete-signal listeners are registered, even when those
listeners do not act on index records. The SQL deletion removes panel/record links first
and does not delete source snapshots or history projections. Progress messages distinguish
old-index deletion from preparation and insertion, helping locate any remaining memory peak.
