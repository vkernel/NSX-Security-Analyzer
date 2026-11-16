# Collection performance

This guide explains how a collection retrieves NSX evidence, how request pacing and
concurrency protect the NSX Manager, where to find timing data and how to tune a slow
collection. For CPU, memory and capacity planning, see
[resource sizing](resource-sizing.md). For saved VM membership strategy and browsing
performance, see [workspace performance](workspace-performance.md).

Every collection request is a read-only GET. Faster is only better when coverage stays
the same: unknown, unavailable, partial and complete-empty evidence remain distinct
states, and no optimization turns a missing answer into an empty one.

## How a collection runs

### Worker lanes

The collection worker supervises independent subprocess lanes. Each lane runs at most one
task at a time across all worker replicas, enforced with PostgreSQL session advisory
locks:

| Lane | Work |
| --- | --- |
| Collection | One collection across all environments |
| Recalculation | Finding-review indexing and criteria recalculation |
| Comparison | Snapshot comparisons |
| Deletion preview | Read-only NSX deletion previews |
| Deletion execution | Confirmed NSX deletions |
| Exports | Reference exports |

Lanes have separate queues, so a recalculation or comparison can run alongside a
collection. Publication for one environment briefly serializes on its environment lock,
and a newly saved snapshot can make a running recalculation retry against the new
history. Separate Analyzer databases do not share these limits. Stop old worker replicas
before starting upgraded workers so old and new lane behavior cannot overlap.

**Administration → Operations → System health** shows collection, recalculation,
comparison and scheduler heartbeats. Each loop reports at most every 15 seconds; a report
older than 180 seconds is marked **Not reporting**. Heartbeats show supervisor activity,
not collection progress or database health. Kubernetes readiness probes and Docker
healthchecks read local heartbeat files; they do not restart services or query
PostgreSQL.

### Collection stages

The collection page shows the current stage. Percentages count completed stages, not
time remaining.

| Stage | Work | Runs in parallel |
| --- | --- | --- |
| 0 · Reading domains, groups and services | Domains, then every domain's group list and the service list | Group and service lists |
| 1 · Scanning references, firewall counters and VM associations | Starts three retrieval branches: configuration search, DFW rules and counters, and membership | Search, DFW and membership branches |
| 2 · Completing firewall rules and counters | Search finished; DFW continues | |
| 3 · Collecting VM associations and checking group membership | DFW finished; membership continues | |
| 4 · Checking tags and VM assignments | Reference analysis, tag analysis | |
| 4 · Collecting network members and linking VM interface attachments | Network member lists and VM interface inventory for network-based groups | Member lists across groups |
| 5 · Preparing report and hit history | Build the report, carry forward hit history | |
| 6 · Saving snapshot | Save, index and synchronize findings | |
| 7 · Completed | **Completed** or **Completed with coverage warnings** | |

The membership branch first reads the tag catalog (`/infra/tags`) and VM inventory
(`/infra/realized-state/virtual-machines`) in parallel, then reads each VM's group
associations, then probes membership for groups that no association already proved
non-empty.

Testing samples and single-worker runs stay sequential. All branches share one request
limiter and one pacer, so running them together does not raise the request rate ceiling.

### Live progress counts

Long branches append live counts to the stage text, for example
`groups checked 1,200/4,800 · VM associations 2,950/3,000`. The counters are:

- **groups checked**: membership probes completed out of all audited groups.
- **VM associations**: VM association reads completed out of collected VMs.
- **network member lists**: network member-list reads completed out of planned reads
  (selected network groups × member categories).

Counts update at most every 10 seconds, and immediately when a branch completes. A count
can stand still while requests wait for pacing or throttling.

### Prior-run hints

The worker reads a compact projection of the previous snapshot: membership endpoint
hints, statistics cooldowns and rule identity and hit-history fields. Previous evidence
only orders probes or skips known-failing optional endpoints; NSX is always queried
again, and negative membership is never reused.

**Warm start.** When the previous collection against the same manager finished with
no HTTP 429 throttling and no concurrency reduction, the next collection starts at 75%
of its final request rate and concurrency, never above the environment's maximum.
Otherwise it starts cold (10 requests/s, concurrency 2). Throttling and overload still
reduce both immediately. The applied values are saved in
`performance.warm_start`.

### Publication

After retrieval, the worker prepares the report and publishes it in one transaction:
the snapshot (`write_snapshot`), its findings (`synchronize_findings`) and the
collection's success. A collection that is stopped or has expired cannot publish a late
result, and earlier snapshots stay available.

The browsing index (`index_snapshot`) is built right after that commit, in the same
process. Meanwhile the snapshot shows **Preparing browsing data**; findings and reviews
are already available. If indexing fails, or the collector is stopped or times out
while indexing, the published snapshot is handed to the background rebuild (the same
path used to restore archived snapshots), so the collection is not lost.

Finding publication advances observations, eligibility and workflow state on every
collection, but rewrites saved JSON evidence only when its fingerprint changes. Index
writes use batches of up to 500 records and an estimated 4 MiB payload; a single
oversized record is written alone. Finding updates and their review events use batches
of 100 under the environment lock.

## Request pacing and concurrency

Each request passes three gates in order: the adaptive concurrency limiter, the shared
request pacer and the per-request retry policy. All collection threads share the same
gates, and waiting requests are served in FIFO order, so a thread finishing one request
cannot jump ahead of already-queued work.

### Maximum concurrent NSX requests

Set **Maximum concurrent NSX requests** in **Edit environment → Advanced request
settings**: default **8**, range **1–32**. The value is saved per environment and
captured when a collection is queued; queued or running collections keep their captured
value, and older queued jobs without it use 8. No restart, ConfigMap or environment
variable is needed.

This is a ceiling, not a target. It also sets the size of the worker thread pools,
including the firewall counter pool, while all HTTP requests still pass through the
shared limiter. The HTTPS connection pool holds up to 16 connections per collection,
reuses TLS connections, rejects redirects, honors system proxy settings and closes when
the collection finishes.

### Adaptive concurrency

| Behavior | Rule |
| --- | --- |
| Start | 2 concurrent requests (or the maximum, if lower) |
| Increase | +1 after `max(8, 4 × current limit)` successes while requests were waiting, at least 30 seconds after the last reduction, up to the maximum |
| Reduce on overload | Halve (minimum 1) on HTTP 429, 502, 503 or 504, or a transport error. A burst of overload errors within 5 seconds causes one reduction. |
| Reduce on latency | Halve after three consecutive responses that are much slower than that endpoint's own baseline, at most once every 30 seconds |
| Not overload | Local time-budget errors (the request never reached NSX), read timeouts of optional bulk policy statistics, and optional member-page timeouts |

Latency baselines are tracked per endpoint category, so naturally slow statistics calls
do not throttle other endpoints.

### Shared request pacer

| Behavior | Rule |
| --- | --- |
| Start | 10 requests per second, no initial burst |
| Increase | +25% (at least +1/s) after 20 consecutive successes and 10 seconds since the last change, up to 40/s |
| HTTP 429 | Halve the rate (minimum 0.5/s) and start a shared cooldown. Simultaneous rejections during one cooldown halve the rate once. The cooldown honors numeric or HTTP-date `Retry-After`. |
| After throttling | Growth waits until 30 seconds after the shared cooldown ends |

These are conservative application values, not NSX limits. Shared clients or expensive
endpoints can still trigger throttling.

### Retries

- **HTTP 429** waits on the shared pacer's cooldown and is retried without using one of
  the environment's retry attempts (up to 20 throttled retries).
- **Transport errors** (connection reset, read timeout) are retried once after one to
  two seconds.
- **HTTP 502, 503 and 504** (and 429 when the shared pacer is unavailable) use the
  environment's retry count, with exponential backoff plus jitter or `Retry-After`.
- Bulk policy statistics and optional member-detail pages are not retried.
- Each request has a 60-second retry budget. Time spent waiting for concurrency or
  pacing does not consume it.

### Collection deadline and post-processing reserve

The worker time limit is `NSX_AUDIT_TIMEOUT` (default 3,600 seconds). Retrieval stops
early enough to save, index and publish the snapshot before that limit:

```text
reserve = min(max(300 s, 15% of the limit), 40% of the limit)
```

| Worker limit | Reserve | Retrieval deadline |
| --- | --- | --- |
| 600 s | 240 s | 360 s |
| 3,600 s (default) | 540 s | 3,060 s |
| 7,200 s | 1,080 s | 6,120 s |

All waits are bounded by the retrieval deadline. Reaching it aborts the collection
rather than recording remaining checks as unknown; earlier snapshots stay available. The
worker's own timeout and **Stop collection** remain effective.

## Membership and network collection

### VM group associations

Each collected VM's group associations are read from
`virtual-machine-group-associations` by exact external ID: normally one request per VM,
paginated with 1,000 results per page and up to 100 pages. The first VM of each scope is
read before the parallel fan-out, and a 401, 403, 405 or 501 response disables that
scope instead of repeating the error for every VM. Ambiguous VM identities are not
queried. Names and IP addresses never identify membership.

A positive association proves that a group is non-empty, so that group needs no
membership probe. Missing or empty associations never establish that a group is empty.

### Group membership probes

Groups without a positive association are probed through the NSX group member
endpoints.

- **Order:** the endpoint that found members in the previous snapshot, then endpoints
  suggested by the group's member types, then the remaining supported endpoints. A
  positive result ends the check.
- **Type-specific plans:** a simple flat condition with one known member type skips
  inapplicable endpoints. VirtualMachine keeps VM, IP and logical-port probes; Segment
  keeps logical-switch, IP and logical-port; VIF and SegmentPort keep IP and logical-port.
  Mixed types, nested or path expressions, extended criteria and uncertain definitions
  keep all four probes. See Broadcom's
  [VM members](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_GetGroupVMMembers.html),
  [logical-switch members](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_GetGroupLSMembers.html)
  and [logical-port members](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_GetGroupLPMembers.html).
- **Negative evidence:** every required negative probe must succeed without an
  unexplained continuation cursor. A failed probe stays unknown unless another endpoint
  returns members. Prior empty results are never reused.
- **Duplicates:** identical repeated group paths are probed once; conflicting
  definitions for the same path fail the collection.

**Restricted VM fast path.** A local group with exactly one positive VirtualMachine
Name or Tag `EQUALS` condition can be established empty from one fresh VM membership
response with an explicit `result_count` of zero, an empty result list and no cursor;
the IP and port probes are omitted and noted. Remote, mixed, nested and extended
definitions, missing counts, cursors and failures always use the full plan. About 5% of
eligible groups get full verification, rotating daily in UTC, and Sunday verifies every
group. A verification discrepancy rechecks every shortcut result before publication and
disables the shortcut for later collections.

### Network member lists and VM attachments

Groups whose criteria involve segments, segment ports or interfaces (including through
referenced parent groups) also save their effective segment, port and IP member lists.
VM-only groups use the direct association path and add no network requests.

- Selected network groups request up to 1,000 members in their initial probe, and those
  pages are reused. Other pages use 1,000 records and verified cursors, up to 100 pages
  per category; at that limit coverage is explicitly partial.
- Modern segment and port endpoints fall back to legacy endpoints on compatibility
  failures. Empty successful lists are checked against the alternate endpoint; positive
  lists are not. A recovered endpoint choice is shared per scope and category, and
  unsupported or denied endpoints are not probed again. Diagnostics count
  `empty_response_checks` and `empty_response_recoveries`.
- Permission and page failures keep positive evidence without establishing a complete
  zero. Negative attachment results never establish cleanup or deletion eligibility.

Ports are joined to VM interfaces by exact `owner_vm_id`, `lport_attachment_id`, Policy
`attachment.id` and canonical port paths, using one shared Management Plane VIF
inventory read through the same pool, pacer and limiter. That VIF read starts with the
network phase and runs alongside the group member-list reads. If owners use fabric VM IDs, one
fabric VM inventory bridges them to Policy VMs. Names, MAC and IP addresses are never
identity fallbacks; ambiguous owners, conflicting interfaces and unresolvable legacy
port IDs stay unknown. Port configuration already returned by search is reused, and
remaining segment inventories are fetched once and shared. There is no per-VM × group
cross-product and no realized-state scan.

If both port endpoints are empty but a saved interface attachment points to a member
segment, attachment coverage stays partial. Nested AND/OR conditions are never bypassed
by inheriting a child's links. See
[NSX group member categories](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/inventory_group_members.html).

### VM interface addresses

The same paginated VIF read saves each VM's interface addresses from
[`ip_address_info[].ip_addresses`](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_ListVifs.html)
in the VM's `network_interfaces`, with source, owner identity, timestamp and coverage.
Guest information is not an address source. Conflicting or ambiguous owners are
omitted. Collection logs `Saved VM address coverage`. Browsing uses these addresses for
saved IP/subnet references; that index is built on first browse, not during collection.
If NSX returns no usable interface IPs, the VM shows **Unavailable**.

## Counters and statistics

Rule hit counters are read per policy in bulk where possible, with per-rule fallback.

- **Bulk policy statistics** use an eight-second connect/read timeout (or the environment
  timeout if shorter) and one attempt. Only one bulk probe runs at a time; the remaining
  counter workers serve rule fallbacks, which start as soon as their policy's bulk
  request finishes.
- **Fallback** to individual rules happens for incomplete, ambiguous or malformed bulk
  evidence. Bulk entries are mapped by exact rule path, or by `internal_rule_id` to the
  system-assigned numeric `rule_id` within the same policy. Display names and
  user-supplied IDs are never used; duplicate or conflicting mappings fall back. See the
  [RuleStatistics schema](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/types_RuleStatistics.html).
- **Cooldowns** for failed bulk endpoints are saved with the snapshot: transient
  failures start at one hour, HTTP 400/404/405/501 at six hours, doubling to at most
  24 hours. Responses without an unambiguous rule identity get a 30-minute cooldown.
  After expiry the endpoint is probed again; a complete success clears its history.
  Cooldowns are failure records, never cached counters. Individual rule counters are
  always fetched fresh.
- **Timeout recovery:** after the first pass, rules whose counters timed out get one
  recovery pass with at most two concurrent requests and a shared 30-second budget.
  Rules that still fail keep unknown evidence. HTTP errors and malformed results are not
  retried here. Diagnostics are in `statistics_retry`.
- **Unsupported statistics:** Ethernet rules are kept with **Not supported** activity and
  are not queried. An NSX HTTP 400 with error 500209 is also treated as unsupported. This
  is not zero traffic and never qualifies as zero-activity evidence.

Cooldown history comes from the latest successful non-testing snapshot from the same
Manager. If a collection fails before saving, its new cooldown information is lost;
retention can also remove history, after which the next collection probes normally.

## Search and coverage verification

Configuration references come from the NSX Policy search API, verified against the
directly collected groups and services.

1. Queries exclude `VirtualMachine`, `VirtualNetworkInterface` and
   `GenericPolicyRealizedResource` with flat `NOT` clauses, then flat `!` clauses. If
   NSX rejects a query (HTTP 400) or returns nothing despite known group/service
   inventory, the next form is tried: `resource_type:*`, then `*` (runtime types removed
   locally after pagination is verified), then an explicit list of configuration types.
   The explicit list is a compatibility mode with limited coverage, recorded as such.
2. Requests sort by ascending `path`; if NSX rejects sorting, the same query runs
   unsorted. See the
   [NSX Search API](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_QuerySearch.html).
3. Pagination continues until the unique count matches the reported total or the cursor
   ends. Duplicates are identified by resource type, origin site and Policy path (or
   parent-scoped ID); identical repeats are dropped, conflicting versions trigger a
   restart from page one after two and four seconds.
4. If directly collected groups or services are missing from the results, the whole
   search is retried up to three times (waiting two and four seconds).

All query alternatives and retries share a budget of five search scans. Partial results
are never accepted. Persistent gaps fail the collection with missing group/service
counts, returned record counts, the query and up to three missing path samples. A gap
alone does not prove indexing lag; query behavior or path scope can explain it.

### Inventory changes during collection

Objects created or deleted while the collector pages through an inventory can change
its count. A count mismatch or duplicate without a reported total retries that inventory
from page one twice (after two and four seconds), discarding the previous attempt. If it
still disagrees, the collection finishes with **coverage warnings** and the affected
endpoints appear under the report's **Coverage**. A small difference, such as 38,399
received versus 38,409 reported, is still unverified. Failed requests, invalid responses,
repeated cursors and deadline exhaustion remain retrieval errors.

Conflicting-version warnings list up to three object identities and up to 20 changed
top-level field names per object, without values. For duplicate VM records from the
realized-state VM inventory, changes only to `_last_sync_time`, `compute_ids`,
`guest_info`, `host_id`, `local_id_on_host`, `power_state` or `source` do not invalidate
tag verification; those values are first-observed, not an atomic VM snapshot. Any other
field difference still triggers retries.

Inventory warnings pause only the findings that depend on the affected evidence; see
[inventory warnings in the review workflow](finding-review-workflow.md#inventory-warnings-pause-affected-findings).
Allow changes to settle, then collect again. Persistent mismatches in a quiet
environment warrant checking NSX search health and API visibility.

## Diagnostics

Open **Collections → Diagnostics** on a collection (operator or administrator) for
request statistics, retries, concurrency and stage timings. Request statistics are
available while the source snapshot is retained; for live or interrupted work, use the
worker logs. Older snapshots may lack newer metrics.

### Where to find the numbers

| Location | Contents |
| --- | --- |
| `performance.phases_seconds` | `inventory`, `search`, `dfw`, `search_and_dfw`, `membership`, `vm_associations`, `parallel_retrieval`, `references`, `tags`, `network_membership` |
| Publication timeline | `retrieve_nsx_inventory`, `prepare_snapshot`, `write_snapshot`, `index_snapshot`, `synchronize_findings`, `wait_for_environment_lock` and commit time |
| `performance.concurrency` | Initial, peak and final limit, increases, decreases, per-endpoint queue wait, and per-endpoint successes, failures by status, p95 and maximum latency |
| `performance.request_pacing` | Completed and failed attempts, average and final rate, throttled attempts, aggregate pacing wait |
| `performance.membership` | Groups checked, probe calls, probes per group, outcomes, endpoint responses, typed-plan groups and omitted probes, VM fast-path counters, VM probe comparison, network statistics |
| `dfw.collection_diagnostics` | Policies evaluated, bulk successes, cooldown skips, fallback rules and categorized fallback reasons |
| `performance.collection_scope` | Group rows and unique paths per domain; added/removed group counts versus the previous snapshot from the same Manager, with up to ten samples |
| **Collecting worker** | Version, full revision, build time and configured maximum of the worker that collected the snapshot (older snapshots show "Not recorded") |

### Reading the numbers

- Phase times overlap. `search`, `dfw` and `membership` run concurrently; use
  `search_and_dfw` and `parallel_retrieval` for wall-clock time and never add the parts.
- Request durations overlap too. Endpoint request time, queue wait and aggregate pacing
  wait are summed across threads, not elapsed time. Endpoint timing excludes limiter
  waiting and includes transport time; p95 uses the most recent 512 attempts per
  endpoint category.
- **Membership workload** counts logical probes, which exclude internal HTTP retries. A
  response with no members is not proof of emptiness.
- **VM membership check comparison** counts, without extra requests, whether simple
  VM-only definitions agreed across VM, IP and port probes, had a negative VM response
  followed by positive IP/port evidence, had incomplete follow-up, or never reached the
  VM probe. Zero disagreements in one collection do not prove a VM-only shortcut is safe.
- Bulk-counter fallback codes distinguish missing, unmapped or ambiguous identity,
  incomplete nested counters, invalid counters, missing or duplicate scope counters,
  HTTP errors and cooldown, counted per rule.
- `wait_for_environment_lock` separates contention from evidence processing.

### Logs

Worker logs include, every 30 seconds of activity:

- `NSX request progress`: completed and failed attempts, measured rate, configured rate,
  throttled attempts and aggregate pacing wait.
- `NSX concurrency progress`: limit, maximum, active, waiting and per-endpoint p95.
- `Group membership progress` and `VM association progress`: completed and total.

Concurrency changes, throttling cooldowns, rate increases (only when the rate changes)
and retry exhaustion are logged when they happen, and a final pacing summary is logged
when retrieval ends.

## Tuning and troubleshooting

1. **Measure first.** Compare several full collections of equivalent inventories: total
   elapsed time, phase times, throttled attempts, concurrency reductions, bulk
   success/skip and fallback counts, and unknown statistics or membership. A faster run
   is not an improvement if coverage deteriorates.
2. **Raise the ceiling carefully.** Start with the default 8. If NSX latency stays flat,
   there are no 429s and the adaptive limit reaches the ceiling, compare equivalent
   collections at 12 and 16. Do not expect proportional speedups.
3. **Back off on throttling.** Frequent 429s, concurrency reductions or rising p95
   latency mean NSX or a shared client is saturated. Lower the ceiling or move the
   schedule. More retries or higher NSX API limits do not replace sustainable pacing.
4. **Membership-heavy collections.** Check probes per group, typed-plan groups and VM
   association statuses. A denied association scope or many ambiguous VM identities
   moves work to group probes.
5. **Search coverage failures.** Read the missing-path samples and the query mode. Allow
   inventory changes to settle and collect again; check NSX search health when gaps
   persist.
6. **Timeouts near the limit.** Compare `parallel_retrieval` with the retrieval deadline.
   Raising `NSX_AUDIT_TIMEOUT` also raises the reserve. Never raise concurrency to
   compensate for incomplete evidence.

Compatibility-search limits and unsupported extended-identity membership are coverage
limitations, not pacing failures; resolving timeouts does not make them complete.

## Snapshot preparation and report browsing

Report pages read prepared data from PostgreSQL and never query NSX.

- Each snapshot keeps its original JSONB report plus separate summary panels, table
  records, evidence, coverage summaries and indexed tag/scope relationships. This uses
  extra storage and is deleted with the snapshot.
- Tables load 25, 50 or 100 rows; search, filters and sorting run in PostgreSQL with a
  15-second statement limit. Plain-text search supports AND, OR and quoted phrases;
  regular expressions use PostgreSQL syntax. Column suggestions return at most 100
  values. Row counts are reused for five minutes until the search or filters change.
- CSV exports stream all matching rows in the current order, with evidence as JSON in
  the final column.
- Evidence, VM relationships and tag/scope relationships load on demand in pages of 25.
  The browser keeps at most 30 relationship responses (about 2 MB) per page.
- Historical activity saves an assessment for up to five minutes per time window;
  snapshot and collection changes invalidate it.
- The first comparison of a snapshot pair runs in the comparison lane; results are
  saved and paginated. Logs show `projection_seconds`, `compare_seconds` and
  `persist_seconds`.
- Web logs record a `Snapshot page` entry with `mode=indexed` or `mode=legacy`,
  metadata, presentation and render timings, and response size. `mode=legacy` means the
  snapshot is not indexed. A long metadata time points to database latency; compare with
  browser network timing to separate server work from ingress and rendering.

### Saved VM relationships

VM pages keep three kinds of evidence distinguishable. None of them describes effective
firewall policy or observed traffic.

- **Direct associations.** **Related groups** on a VM and **VMs** on a group come from the
  saved VM group associations.
- **Network links.** Network groups show **Attached VMs** for verified interface → port →
  segment attachments; a zero direct VM count can coexist with attached VMs. Drawers name
  the basis and keep the attachment provenance.
- **IP/subnet matches.** VM References lists groups whose saved address definitions
  match the VM's saved interface addresses, and firewall references through them. This
  is configuration evidence, not group membership or current IP ownership, and does not
  change Related groups counts.

IP matching supports IPv4 and IPv6 hosts, CIDRs and inclusive ranges. A group must
contain one explicit `IPAddressExpression`, or one `PathExpression` that references only
supported IP-only child groups in the same scope; cycles, missing children and mixed or
extended conditions stay unresolved, and an IP branch never bypasses another condition.
Shared addresses in the same scope, and link-local, loopback, multicast, unspecified or
invalid addresses are excluded with a note. Excluded rule source/destination fields are
shown as negated references. Direct rule IP literals and ANY-only rules are outside this
path. The address index is built on first browse per snapshot and bounded to 32 addresses
per VM, 100,000 intervals per snapshot, 32 nesting levels and 2,000 matching intervals per
lookup; limit hits keep known matches and mark coverage partial. See the
[NSX IP expression format](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_AddorRemoveGroupIPAddresses.html).

**Current NSX** mode refreshes only direct VM associations. Firewall references then
combine them with saved attachments and saved IP matches, labelled **Current VM
associations + saved attachments**, each with its collection timestamp. Saved links never
enter the live cache, and snapshots are not changed.

### Preparing older snapshots

New collections prepare their data automatically. To prepare or refresh existing
snapshots, run the management command in a separately sized maintenance container or
Job, not in the live web container:

```sh
# Docker Compose: run from the folder containing compose.yaml and .env.
docker compose run --rm --no-deps snapshot-refresh python manage.py index_snapshots

# Kubernetes: configure the same command in a maintenance Job.
python manage.py index_snapshots
```

- Without options, only snapshots missing derived data are prepared; already-indexed
  snapshots are skipped and the command can be restarted.
- `--refresh` rebuilds existing indexes (needed after upgrades that change embedded
  controls or projections).
- `--snapshot UUID` limits the run to one snapshot, which helps isolate a failure.

Each snapshot is one transaction: a failed refresh rolls back that snapshot's index. The
old index is deleted with SQL inside the same transaction. Memory is not fixed: source
JSON, lookup maps and layout still occupy memory. Progress logs bracket JSON loading with
`stage=load_report_start` and `stage=load_report_complete`, add checkpoints for history
projection, layout, row insertion and saving, and report `peak_rss_mib` (process
lifetime high-water RSS, not current pod usage). Until prepared, older snapshots show an
explicit notice rather than a misleading result. See
[snapshot maintenance](snapshot-maintenance.md#kubernetes-create-a-temporary-job-manually)
and the
[Kubernetes guide](../deploy/kubernetes/README.md#preparing-older-snapshots-for-faster-report-pages).

## Historical measurements

These figures are dated regression evidence from synthetic fixtures or specific test
environments. They are not production latency or duration predictions; measure your own
environment with a new collection of a comparable workload.

| Date | Scenario | Result | Source |
| --- | --- | --- | --- |
| 2026-11-10 | Synthetic: two network groups sharing 44 segments, 605 ports, 844 IP entries, three VMs | 49 reads when 44 segment inventories must be fetched; 5 when search already supplies port configuration. Identical VM relationships in both modes. Mock call counts. | [network-membership-2026-11-10.json](performance/network-membership-2026-11-10.json) |
| 2026-11-10 | Synthetic: 12 network groups with one compatibility recovery, 3 and 3,000 VMs | 38 network requests in both cases, one shared VIF inventory, no segment inventory reads; healthy phase stays at 5 requests | [attachment-reference-fixes-2026-11-10.json](performance/attachment-reference-fixes-2026-11-10.json) |
| 2026-11-10 | Synthetic browser acceptance of network membership | Zero direct VM members, two attached VMs, member totals, paging and saved firewall references without live membership requests | [network-membership-browser-2026-11-10.json](performance/network-membership-browser-2026-11-10.json) |
| 2026-11-10 | Synthetic browser acceptance of current references | Empty current association cache keeps the saved attachment-based rule with its timestamp, without a live membership POST | [attachment-reference-browser-2026-11-10.json](performance/attachment-reference-browser-2026-11-10.json) |
| 2026-11-11 | Synthetic: 3,000 VMs, no network-group criteria | Three VIF pages instead of 3,000 per-VM requests | — |
| 2026-11-11 | Synthetic browser acceptance of VIF address capture | Subnet group and firewall rule found after full collection, zero live membership requests | [vif-address-browser-2026-11-11.json](performance/vif-address-browser-2026-11-11.json) |
| 2026-11-11 | Local synthetic IP-reference benchmark: 10,000 groups, 3,000 VMs | 0.573 s initial index build; 5.497 ms median over 20 warm lookups; seven queries per lookup; no NSX calls | [ip-reference-benchmark-2026-11-11.json](performance/ip-reference-benchmark-2026-11-11.json) |
| 2026-11-11 | Synthetic browser acceptance of IP references | Four IP group matches and six firewall references while the confirmed group count stays at one | [ip-reference-browser-2026-11-11.json](performance/ip-reference-browser-2026-11-11.json) |
| Undated | Synthetic Python allocation: 1,000 VMs × 100 related rules during index refresh | 24.61 MiB materialized versus 0.18 MiB incremental, excluding source report and inserts | — |

## Upgrade notes

Apply migrations through the normal deployment step before starting matching web and
worker versions, then start a new collection: running collectors keep their old
behavior, and existing snapshots are never repaired retroactively.

- `0018_snapshot_presentation`, `0019_snapshot_latest_index`: prepared report
  presentation; run `index_snapshots` for older snapshots.
- `0022`, `0023`: shared comparison storage and pagination indexes (index creation can
  take time on a large history).
- `0024`: coverage summaries; prepare older snapshots with `index_snapshots`.
- `0027_snapshotvmrule`: shared VM rule definitions; old indexes stay readable.
- `0067_saved_ip_references`: empty IP-reference cache tables; nothing is backfilled.
- VIF address capture needs a collection with a corrected worker; older snapshots cannot
  recover addresses by rebuilding caches.
- Build `2398e23` lacks `index_snapshots --snapshot`, and version 0.5.2 lacks the
  lower-memory refresh; use a current image for maintenance Jobs.
