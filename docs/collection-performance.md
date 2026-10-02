# Collection performance

For CPU, memory and capacity planning, see [small, medium and large resource sizing](resource-sizing.md).

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
docker compose run --rm --no-deps snapshot-refresh python manage.py index_snapshots

# Kubernetes: command to configure in an optional, separately sized maintenance Job.
python manage.py index_snapshots
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
docker compose run --rm --no-deps snapshot-refresh python manage.py index_snapshots --refresh
```

In Kubernetes, configure the same management command in a separately sized, on-demand
maintenance Job following the [manual maintenance procedure](snapshot-maintenance.md#kubernetes-create-a-temporary-job-manually):

```sh
python manage.py index_snapshots --refresh
```

Use the application image and database/Secret settings for the Job. See the
[Kubernetes guide](../deploy/kubernetes/README.md#preparing-older-snapshots-for-faster-report-pages).
The Compose command creates a separate temporary container using the optional snapshot-refresh service
configuration; include your usual override files and allocate sufficient memory.
This processes saved data without
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

### Diagnose preparation-stage OOMs

Index preparation now emits rows to the database as they are produced, and generates
VM relationship rows one VM at a time. It no longer retains a complete expanded VM
relationship list during refresh. Source snapshot JSON, lookup maps, layout strings
and ordinal/panel mappings still consume memory; this is not a fixed-memory JSON reader.

`stage=load_report_start` and `stage=load_report_complete` bracket JSON loading.
Further checkpoints identify history projection, layout preparation, row insertion and
presentation saving. `peak_rss_mib` is the process lifetime high-water RSS in MiB,
not current usage or total pod usage. An abrupt stop after a start checkpoint helps
locate the next operation to investigate.

To isolate a failing snapshot in the separate maintenance Job, use the updated build
and replace its command with:

```yaml
command: [python, manage.py, index_snapshots, --refresh, --snapshot, YOUR-SNAPSHOT-UUID]
```

The `--snapshot` option is not present in build `2398e23`. Refreshing one snapshot
leaves other indexes unchanged. Keep the Job logs on failure. A synthetic Python
allocation comparison with 1,000 VMs and 100 related rules per VM measured 24.61 MiB
for the materialized VM list versus 0.18 MiB for incremental consumption, excluding
source report allocation and database inserts. This is not a production memory guarantee.

For the temporary container/Job lifecycle and collection stop controls, see
[snapshot maintenance](snapshot-maintenance.md).

### Environment analysis tabs (development)

Environment snapshot and collection lists use small database projections and page
queries. Collection history has an environment/time index for recent-job lookups.

- **Compare snapshots** opens with selectors only. Select **Compare snapshots** to
  calculate a pair; the first calculation still reads the two saved configuration
  projections and can take time for large inventories. Results are saved in PostgreSQL,
  shared across web pods and paginated in SQL on subsequent requests. Selector choices
  are also paginated (100 snapshots per page); older snapshots remain selectable.
  Cached differences consume database storage and cascade when either snapshot is
  deleted by retention. Saving a changed source report invalidates its comparisons.
- **Finding reviews** reads persisted findings without synchronizing or locking the
  environment during GET requests. Collection workers continue to synchronize findings
  when saving a full snapshot. Older installations without a baseline need a new full
  collection. Review POST requests retain locking and revision checks. Opening a finding
  no longer fetches its complete source snapshot, and saved evidence is loaded only
  when the user selects **Load saved finding evidence**.
- **Collection coverage** filters compact indexed statuses, counts issues in PostgreSQL,
  and retrieves detailed notes only for the visible page. Snapshot-level errors remain
  included. Observation gaps still use timestamps within the selected window; these
  queries do not load snapshot payloads. Unprepared legacy snapshots show an explicit
  maintenance notice instead of loading a full report in the page request.

Apply migrations `0022` and `0023` before deploying this build. They add shared comparison
storage and pagination indexes; creating indexes can take time on a large existing
history. No refresh is required for already indexed snapshots to use these changes.

### Prepared coverage summaries (development)

Migration `0024` adds small coverage summaries and issue rows. New collections prepare
these from the already-loaded report in the worker, in the same transaction as the
snapshot index. Coverage browsing reads the stored issue count and only the requested
page of plain-text issue rows. It no longer extracts fields from report JSON, scans
inventory JSON statuses or sorts every inventory record before showing a page.

After migration and rollout, either run a new collection or prepare missing coverage
for an existing snapshot using the separate maintenance container/Job:

```sh
python manage.py index_snapshots --snapshot YOUR-SNAPSHOT-UUID
```

Omit `--refresh`: existing inventory indexes are reused. Omitting `--snapshot` prepares
all snapshots missing derived data. This backfill still loads each source report once,
so use the [maintenance window and resource guidance](snapshot-maintenance.md). It is
not run by the schema migration or a page request. Until prepared, older snapshots show
an explicit coverage-pending message, never a misleading zero-issues result.

The wider page review also removes snapshot joins from live collection polling and
stops extracting snapshot summaries on collection-list pages. Notifications request
their coverage indicator explicitly. First uncached comparisons and historical activity
assessments still involve larger calculations; their existing saved-result caches apply.

### VM relationship details

VM relationship dialogs open without fetching expanded evidence. Tags, groups and
deduplicated rules load on expansion in pages of 25. Rule details and their complete
“via group” relationships are fetched separately; raw VM metadata loads only when
requested. The browser retains at most 30 responses, bounded to approximately 2 MB
of serialized text, for the current page. Large responses are not cached.

Existing prepared snapshots use this interface without a refresh. PostgreSQL extracts
only the requested relationship page; it may still decompress and scan the selected
VM record's JSONB arrays, particularly when deduplicating legacy rule references.
The application does not retrieve or parse the whole snapshot. New collections store
each related rule once per VM while preserving all related group paths.

### Throttling and sustained collection rate

Collection requests start at 10 requests/second with no initial burst. All collection
threads share that pacing gate, in addition to the existing adaptive concurrency
limit. HTTP 429 halves the rate (minimum 0.5/second), starts a shared cooldown and
reduces concurrency. Simultaneous rejections during a cooldown do not repeatedly
halve the rate. Successful traffic recovers by 0.5 requests/second after each
30-second stable interval, up to 10/second. These are conservative application tuning
values, not advertised NSX limits.

Retries honor numeric or HTTP-date `Retry-After` headers and otherwise use exponential
backoff plus randomness. Existing environment retry counts remain the attempt cap;
each request also has a 60-second retry/pacing budget bounded by the collection's
remaining deadline. A server delay exceeding that budget ends the request rather
than retrying early. The worker's overall timeout and Stop collection supervisor
remain effective during waits. Optional bulk statistics still fall back without
retries, but their 429 responses also cool down other requests.

Job claiming is serialized briefly in PostgreSQL and prevents simultaneous running
jobs for the same normalized manager hostname and port across environment entries
and worker replicas. Different DNS aliases or IP addresses for the same manager
cannot be identified automatically; configure a consistent manager address. External
applications and separate Analyzer databases do not share this limiter. Existing
running collections must finish before the new worker behavior takes effect.

Logs show shared cooldowns, rate recovery, retry exhaustion and a final pacing
summary; successful report performance metadata includes throttled attempt counts,
final request rate and aggregate thread wait time. A failed membership request stays
unknown through the existing coverage handling; it is never classified as empty.
Validate the defaults with a representative full collection and monitor coverage,
429 counts and elapsed time before changing limits. More retries or higher NSX API
limits are not a substitute for sustainable request pacing.
