# Workspace performance

Segmentation, VM-to-VM investigations and naming reviews reuse derived snapshot data. They do not contact NSX when opened. Opening VM References can make a targeted read-only membership check when the current cache is missing, stale or incomplete; collection remains independent.

## What changed

- A versioned `SnapshotAnalysisIndex` stores group evidence, projected rule configuration/fingerprints and service definitions in separate columns. New collections prepare it transactionally alongside the browsing index. Older ready snapshots prepare missing components on first use. Web workers share the stored result.
- `SnapshotApplicationMap` caches the assembled topology, coordinates, counts and display labels for the exact application scope/search/service/disabled/hidden-group selection. A cache hit reads compact map data without loading group/rule projections or rebuilding the graph. At most 12 recently created variants are retained per projection. New collections and the preparation command warm the default map for the configured scope.
- Connection summaries contain no inline rule evidence. Opening one fetches up to 25 rule links from the same snapshot and filters; successful results are reused while the page stays open. Failures offer retry. Changed source evidence or an incompatible map version asks the reader to reopen the map. Cache eviction can rebuild a matching view without changing the selected snapshot.
- Grouping settings and an unselected security matrix request groups only. Boundary comparisons run on the priorities view. URLs and review metadata are prepared for displayed rules, rather than every retained rule or group.
- The matrix initially renders at most 50 choices plus selected groups. Search returns at most 50 matches from the pinned snapshot. Selected groups survive subsequent searches; the matrix still accepts at most 12 groups.
- VM membership calculations are reused per snapshot and VM record. Rule matching builds membership lookup dictionaries once per VM, instead of once per rule. Membership drawers load 25 relationships at a time. VM References lazily checks current membership; group member lists retain their explicit live-check controls.
- Naming assessment results are reused separately for observed results and draft simulations. Their signature includes the snapshot, algorithm version, policy configurations/revisions, scope bindings, proposal fields, coverage warning, and date. Policy previews bypass assessment reuse. Exceptions expire and stale proposals still require review.
- Finding, activity, comparison, finding-policy and deletion-plan status requests render only their status regions. My work status requests skip the finding-table query and readiness calculation. Drawer-close refreshes use the same fragment contract. Existing refresh intervals, selection preservation and error handling continue to apply.
- Group/rule projection queries have partial indexes on snapshot and ordinal. Migration 0063 creates these concurrently on PostgreSQL so normal collection inserts can continue.

## Cache lifecycle

The caches are disposable. Archiving/rebuilding the browsing index deletes them; restoring it prepares a fresh projection. Deleting the snapshot cascades to its derived rows, including assembled maps. A projection version change discards dependent maps/membership/naming caches. Saving changed record data also invalidates them; changes to unrelated index metadata do not. Grouping-scope changes select a different map-cache key.

## Saved VM associations during collection · 5 November 2026

The [network attachment correction](collection-performance.md#network-member-lists-and-vm-attachments)
extends this direct-VM strategy with separate saved segment/port evidence and exact
VM interface ownership. Related-group counts and saved firewall references now use
both verified relationship types, while direct VM member lists remain distinct.

The prior probe-only collection saved at most the first identity returned by an
existence check. Positive IP checks could skip the VM endpoint entirely. A clean
collection therefore did not imply complete saved VM membership. Inverting those
samples produced zero confirmed groups with unknown totals, and could omit saved
firewall references. Live checks used a different direct association API and never
repaired historical snapshot rows. The earlier live-browsing optimizations did not
address this collection defect.

Collection now reads the existing VM inventory once, then requests
`/infra/virtual-machine-group-associations` for each unique VM external ID, using
1,000-reference pages and validating continuations. The same collection client,
request pacing, adaptive concurrency and overall deadline apply. There are no
per-group full IP/port/segment downloads or per-edge HTTP requests. A positive
association replaces that group's nonempty probe; absent associations do not
bypass existing cleanup checks. Search and DFW retrieval still overlap this work.
Permission/unsupported responses suppress fan-out for that scope. Missing external
IDs, duplicate identities, malformed/changing pages and failed reads remain
explicitly incomplete. Collection-deadline errors still fail the collection.

Saved VM counts, Groups references, inverse group VM lists and saved firewall
references use the same edges. Completeness is per VM, so a failed lookup does not
downgrade another VM's verified list. Inverse group lists are complete only within
the collected visible VM inventory and default enforcement point, and require all
its lookups and inventory to be complete. System/default cleanup exclusions do not
exclude a returned VM association. RBAC-hidden objects and other enforcement points
remain outside scope, and NSX reads are not atomic. No tag/name/IP membership
inference or effective-policy assertion is made.

New association-based VM rows omit obsolete tag-candidate group/rule expansion;
assigned tags and independent tag reports remain available. Complete saved VM
references default to Saved snapshot without an automatic live request. Partial
older snapshots retain the separate Current NSX option. Existing snapshots are
unchanged: reindexing or a current check cannot reconstruct missing historical
membership; a fresh collection on the updated worker is needed. No additional
schema migration is required by this collection change.

`VM association progress` logs and Collection diagnostics report logical reads,
per-status VM totals, elapsed time and reused positive group evidence. HTTP retries
are separate. The collection summary also shows complete VM lookups versus total.

The [synthetic comparison](performance/saved-vm-associations-2026-11-05.json) uses
1,200 VMs and 4,493 groups. Distributed memberships required 1,693 membership reads
instead of 4,493 existence probes, while saving all four memberships per VM. With
all VMs sharing four groups, complete associations required 5,689 reads instead of
4,493 sampled probes. Thus this does **not** promise every collection will be
faster than the incomplete probe-only design. It avoids repeated positive probes
and full endpoint expansion, but exact per-VM evidence can add reads. Real latency,
counter/search work, retries and persistence determine total collection time.
Preparing 250 synthetic VM rows with 500 tag-candidate groups each removed 125,000
obsolete candidate edges and reduced VM row JSON from 10,134,200 to 216,200 bytes.
These are synthetic request/payload measurements, not production runtime claims.

An [isolated Chromium acceptance check](performance/saved-vm-associations-browser-2026-11-05.json)
opens a newly collected/indexed synthetic snapshot with four actual memberships and
25 unrelated tag candidates. It verifies the VM table count, four saved groups,
the matching saved firewall rule and two inverse group VM rows, with zero current
membership endpoint requests and no browser errors.

API contract: [NSX groups for a VM](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_GetGroupsForVM.html).

## Earlier targeted membership observations · 5 November 2026

The earlier implementation performed mandatory cleanup membership checks only. It retained identities already returned by those probes and explicit configured IP entries, but did not expand member pages for browsing. This superseded release `3d91255`'s 300-second/2,000-request detail budget, but still left saved VM memberships incomplete. The direct collection strategy above replaces that sampling behavior. The four `NSX_MEMBERSHIP_DETAIL_*` settings remain removed. Live execution verification, request pacing, adaptive concurrency and the overall deadline are retained.

VM References offers Groups and Firewall rules, with Current NSX selected by default. Assigned tags remain in Summary; the VM Members section, Tag references and Tag-based rules tabs are removed. Saved membership remains available through Saved snapshot and is immutable; incomplete counts say “2 confirmed; total unknown.” When an operator first opens References, a missing, stale or incomplete cache triggers a bounded lookup using `/infra/virtual-machine-group-associations?vm_external_id=...`. It follows opaque cursors and stores compact group references, rather than scanning every group's complete member lists. Group drawers can separately request their VM members using the same cache. Related navigation retains the selected evidence source. IP/port/segment categories remain saved, separate evidence and are not used to infer VM membership.

Current observations are stored in `MembershipLookup` with per-subject coverage and in `MembershipRelation` with one shared VM/group pair. Both directions read the same relations. A complete owner response can retire missing pairs; a partial response retains earlier positive evidence with its individual timestamps. Changes invalidate the opposite owner's completeness. One unrelated incomplete group cannot invalidate a complete direct VM lookup. System/default groups returned by NSX remain visible even though cleanup assessment excludes them. Names, shared tags and IP similarity never establish identity.

Cache reads are authenticated, database-only GETs. POST refreshes require staff access and CSRF. Automatic refreshes use `refresh_mode=if_needed` to reuse a newly completed cache; explicit Refresh always checks again. Current labels include the check time and stale state; observations become stale after five minutes. The namespace changes with manager, credentials or TLS trust, preventing accidental reuse after environment changes. Opening the inventory or a VM Summary makes no membership requests to NSX. The first activation of VM References checks cache freshness and may perform one bounded lookup. Cached positive memberships render immediately with their stale/partial status while this lookup runs in the background; successful checks replace them and failures retain labelled cached evidence. Empty/cold caches still wait for the bounded check, so missing evidence cannot appear as a complete zero. Category switches and cached paging do not repeat that lookup. A newly opened VM drawer checks freshness again; related group drawers retain explicit refresh. Failed automatic checks keep available positive evidence and offer Refresh without retrying on each category switch. Read-only users receive cached evidence and an operator-access explanation instead of forbidden refresh calls. Missing external identity is an explicit error, not a name-based fallback.

VM association refreshes are sequential, with an eight-second transport/queue deadline and at most ten 1,000-row pages. Group-member checks use the progressive paging described below. Missing totals, changed totals, duplicates, cursor loops, failures and cutoffs cannot establish complete membership. Positive partial evidence is retained. The response pages at most 25 relationships. One manager lease serializes enrichment for an environment. New refreshes yield to queued/running collections; already in-flight bounded requests may finish. No cache result authorizes cleanup.

Optional cache warming runs as a separate process, not inside the collection worker:

```sh
python manage.py refresh_membership_cache --environment <environment-id> --limit 100
```

The command processes up to 100 subjects by default (maximum 1,000 per invocation), at most two subjects per second, reuses fresh observations and stops when collection work is pending. It is not scheduled automatically. It does not edit historical snapshots. The API uses the selected environment's Policy API/default enforcement point; source paths retain the matching local/global/project API root where present.

VM References excludes tag-condition candidates entirely. Its Firewall rules table joins confirmed memberships to saved rule Source, Destination and Applied to paths. Current memberships combined with saved rules are labelled explicitly; this is neither live firewall state nor effective connectivity. Rule inspection validates the same confirmed relationship before exposing its saved services and provenance. Rule pages use bounded SQL over indexed snapshot records, never the full report or per-rule NSX calls.

Apply additive migration **0065** to create the two initially empty cache tables. Existing snapshots need no reindex or new collection for targeted current checks, provided they retain a VM external ID. The old saved membership lists are not repaired by live checks. This UI follow-up adds no further migration. Uncollected/partial evidence is never presented as a complete zero; an empty complete NSX response is labelled distinctly.

Validation uses synthetic data only. The four-membership/25-tag-reference test now gets four groups from one targeted request, verifies the inverse group view and preserves the original two-member snapshot. Browser acceptance uses 1,200 VMs and verifies cached round trips, historical isolation and desktop/mobile light/dark layouts. The [10,861-group benchmark](performance/membership-v2-2026-11-05.json) performs 5,430 membership requests, equal to mandatory checks alone, compared with 6,430 for the bounded detail design and 46,702 for the original full-list design. The modeled membership-only time is 135.88 seconds versus 226.26; these are not production runtime predictions. All other inventory, statistics, indexing and NSX latency costs still apply.

### Membership browsing latency follow-up

The browser no longer hides an existing membership page while refreshing it. The first page returned by an explicit group refresh is reused directly, removing a duplicate GET. Firewall responses request coverage metadata without counting, sorting or retrieving a member page that the rule page would immediately replace. Complete coverage metadata uses one database query instead of three. No NSX calls were added to collection and no schema migration is required.

Synthetic checks with a 20 ms application read and 600 ms simulated refresh reduced the wait for cached rows from 624 ms to 23 ms. An actual Chromium check over the isolated 1,200-VM fixture, with an intentionally delayed 1,200 ms synthetic NSX endpoint, displayed stale-labelled groups in 72 ms and completed their refresh in 1,380 ms. Explicit group refresh used one POST without a follow-up GET. All 96 JavaScript tests and 42 membership/reference Django tests pass. These timings describe synthetic cached-first rendering, not production NSX latency.

A separate synthetic 3,239-rule query took 32–52 ms. This does not establish the cause of a production database delay. Before progressive paging, a cold group check could fetch ten 1,000-member pages before displaying the first 25 members. A 10,000-member synthetic group required all ten pages before returning. Progressive paging now fetches a 100-member batch, supplies four 25-row UI pages from it, and follows the server-side NSX cursor only when another batch is needed. Do not truncate a list and label it complete, infer memberships from tags, or move full browsing scans back into collection.

Measurements are in [membership browsing latency](performance/membership-browsing-latency-2026-11-05.json).

### Progressive live group member pages (migration 0066)

An explicit group member refresh starts a `GroupMembershipSession`. The first request normally fetches 100 members and returns 25 rows. Subsequent cached UI pages are database-only GETs; a staff/CSRF-protected continuation POST fetches another batch when the requested window is not yet loaded. The NSX cursor remains on the server. The browser receives a random session ID scoped to the environment/credential/trust namespace, group and saved snapshot. A fresh check supersedes the old traversal; loaded old pages remain readable but cannot consume its cursor.

Each session stores immutable member positions in `GroupMembershipItem`, so appending another batch cannot reorder earlier pages. The UI shows **100 loaded · 4,075 reported by NSX**, or **100 loaded · more available** when NSX omits its total. Reported totals are not a claim that all identities were retrieved. Coverage stays partial until the full traversal passes validation. Partial checks publish positive relationships in both directions without removing older positives; only a complete verified traversal can retire missing pairs. A direct VM observation that changes a group relationship invalidates that group's existing sessions. VM References continues to use its direct association API for complete per-VM coverage, rather than inferring it from partially loaded groups.

Sessions expire after five minutes. Expired, superseded, changed, cross-group/snapshot/namespace and malformed cursors cannot continue. Transport failures retain the unconsumed cursor for Retry; malformed/duplicate/changed-total pages require Refresh. Failed next-page reads leave the prior rows and Previous control usable. Failed initial reads retain earlier positive cache observations with stale/unavailable labels. Read-only users can browse loaded pages but cannot trigger continuation reads. Collection keeps priority.

NSX may return fewer than 100 entries. A request can follow up to ten short pages within the existing eight-second deadline to fill a 25-row display window; it does not skip identities or turn a cutoff into a complete result. A session is capped at 1,000 NSX pages (at most 100,000 entries). Reaching that cap is incomplete and requires Refresh. No work is moved into collection. Pagination detects observable inconsistencies; NSX does not provide an atomic point-in-time snapshot across these calls.

Apply additive migration **0066** before starting the updated application. It creates two initially empty cache tables and their identity/position constraints. It does not rewrite or reindex historical snapshot rows. Cache rows cascade with their lookup/snapshot, and expired sessions are pruned when a new group check starts.

The synthetic 10,000-member browser fixture loaded its initial page in about 180 ms, made one NSX request for the first four UI pages, one additional request at the next batch boundary, and none on Back. These are development measurements with simulated 40 ms NSX latency, not production latency guarantees. See [acceptance measurements](performance/progressive-group-membership-2026-11-05.json). PostgreSQL/SQLite migration checks, all 897 Django tests and 99 JavaScript tests pass, including cursor isolation, stable order, changing totals, short pages, transient failures, partial positives, permissions and direct VM associations.

## Applying the update

Run the normal deployment migration step (`python manage.py migrate`) before starting the updated web/worker processes. This adds migrations 0062–0064. Concurrent index creation in 0063 can take time on a large existing database. Migration 0064 adds the assembled-map cache table without scanning existing source records.

Optionally warm the latest full snapshot per environment after migration:

```sh
python manage.py prepare_analysis
```

To warm one retained ready snapshot instead:

```sh
python manage.py prepare_analysis --snapshot <snapshot-uuid>
```

These commands prepare derived data only. They do not rebuild the full report or make NSX requests. Archived/unfinished snapshots are skipped. New collections prepare the projection automatically.

### Recovering an interrupted migration 0063

An earlier version of 0063 could fail on retry with `relation "snapshot_group_projection" already exists` (or `snapshot_rule_projection`). Its concurrent index operations run outside a transaction, so an index can remain even when Django has not recorded the migration as applied. A cancelled concurrent build can also leave an invalid index.

Use the corrected development image for the migration job and all application components, then rerun the normal migration step inside the updated image:

```sh
python manage.py migrate --noinput
```

The corrected 0063 checks the existing index's table, columns, method and predicate. It reuses a matching valid index and concurrently rebuilds a matching invalid one. Snapshot rows are preserved. A conflicting relation definition stops the migration with an explanatory error and is left untouched. Run one deployment migration job at a time; a second migrator now stops with a clear error instead of waiting for the first migrator's advisory lock. If another index build is still active, let it finish before retrying. Existing databases that already recorded 0063 as applied need no additional migration.

Do not fake the migration or remove snapshot tables to bypass this error: an existing index may be invalid, or the other required index may still be missing.

### Diagnosing a migration that appears stuck

Three minutes of `Running migrations:` output alone does not identify the cause. Django writes its `Applying ...` status without a newline until the operation finishes; a log viewer may not display an unfinished line. Corrected 0063 logs a separate line when checking, reusing, removing or building each index, and another when a build finishes. Confirm the migration pod uses the corrected image revision, rather than relying on a cached mutable `development` tag.

Migration 0063 also emits a structured `progress` log every **15 seconds** while an index operation runs. Each line includes the index name, migration backend PID, operation stage, elapsed seconds, PostgreSQL index-build phase, tuple/block/locker counters, wait type/event (for example `IO` / `WalWrite`), and blocking session IDs with their state and transaction age. An `input_size` event reports estimated source rows and heap/total bytes using catalog estimates and relation sizes, without counting or reading snapshot rows. `started`, `finished` and `failed` events bracket each operation; database failures include SQLSTATE.

`phase_observed_seconds` measures time since the observer first saw the current stage/phase. `progress_changed: false` means its phase/counters were unchanged between samples; it is not a verdict that the build is stuck. Counters can be zero or unavailable in some phases, so logs do not invent an overall percentage or completion estimate. A `WalWrite` sample records the current wait, not the cumulative time spent waiting for storage.

The observer uses a separate autocommit, read-only connection, with configured connection/query/lock timeouts capped at 3/2/1 seconds. It cannot hold a long transaction snapshot or change migration connection timeouts. Observation errors produce `observer_unavailable` warnings and leave index work running. No SQL text, snapshot contents or credentials are logged. Normal diagnostics add one activity query per sample, an extra bounded blocker query when needed, and one source-size lookup per index operation. PostgreSQL permissions may limit visible activity; logs expose `activity_visible` and report unavailable diagnostics explicitly.

Follow these logs with:

```sh
kubectl -n nsx-security-analyzer logs -f job/migrate --timestamps
```

The Kubernetes migration Job now has a **3600-second (one-hour) deadline**, increased from 900 seconds to allow concurrent index builds over retained snapshots to finish. The 15-second progress logging remains enabled. Logged elapsed time is per index operation; the Job deadline applies to the entire Job. If Kubernetes terminates the pod, it may not emit a final application `failed` event; retain the last progress lines and the Job's `DeadlineExceeded` status. The existing retry recovery still handles matching invalid indexes left by interrupted builds. Recreate a failed migration Job through the normal deployment process using the updated image, after checking that the previous pod has stopped and that no earlier migration backend is still building an index. A running earlier backend must be investigated before retrying.

Concurrent index creation scans the source table and can wait for older transactions. A running Kubernetes pod only confirms that its process is alive. Inspect these read-only queries in the application's PostgreSQL database (use its existing database login):

```sql
SELECT pid, phase, current_locker_pid, blocks_done, blocks_total
FROM pg_stat_progress_create_index
WHERE datname = current_database();

SELECT pid, state, wait_event_type, wait_event,
       clock_timestamp() - query_start AS query_age,
       clock_timestamp() - xact_start AS transaction_age,
       pg_blocking_pids(pid) AS blocked_by
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state <> 'idle';
```

Repeat after a short interval to distinguish advancing build work from a wait. `waiting for writers ...` or `waiting for old snapshots` identifies a transaction wait; `current_locker_pid` identifies the session being waited on. An `advisory` wait can indicate another migrator using an earlier image. Investigate the identified session before changing application replicas or cancelling anything. Empty results do not prove the migration finished: also inspect pod logs, restarts, CPU/memory and the actual image revision.

PostgreSQL documents [index-build progress phases](https://www.postgresql.org/docs/17/progress-reporting.html#CREATE-INDEX-PROGRESS-REPORTING) and [concurrent index-build waits](https://www.postgresql.org/docs/17/sql-createindex.html#SQL-CREATEINDEX-CONCURRENTLY). The one-hour deadline is a deployment budget, not a performance guarantee; use the observed build progress and transaction waits to diagnose slow runs. Adopt the updated manifest through the normal deployment sync; no Docker image rebuild is required for the deadline change. Repeatedly restarting a healthy large build can prevent it from finishing.

## Measuring a deployed slowdown

Response `Server-Timing` headers expose total, SQL execution, analysis and template-render durations in milliseconds. Structured request logs also include `database_queries`, `response_bytes`, `database_seconds`, `analysis_seconds` and `render_seconds`. Analysis/render timings apply to the instrumented workspaces; other routes may report zero for those stages. SQL timings can overlap analysis/render, so these values should not be added together. Streamed responses omit the response-size measurement.

`Snapshot analysis projection` logs include a cache-hit flag, selected components and load/build duration. `Assembled application map` records map-cache hits and load/build durations. Timings record no SQL text or query parameters. Use these measurements and PostgreSQL query plans to distinguish database delays, computation, rendering and browser/network delays before changing worker counts or timeouts.

## Verification

The initial synthetic comparison used 5,200 groups and 3,240 simple firewall rules on disposable SQLite data. Approximate repeat-request results:

| View | Before | After | HTML before → after |
| --- | ---: | ---: | ---: |
| Application map | 0.33 s | 0.11 s | 184 → 184 KiB |
| Grouping settings | 0.15 s | 0.05 s | 20 → 20 KiB |
| Matrix picker | 0.22 s | 0.05 s | 1,025 → 27 KiB |
| VM-to-VM rules | 0.51 s | 0.09 s | 740 → 53 KiB |
| Naming compliance | 0.18 s | 0.09 s | 47 → 47 KiB |

After assembled-map caching and lazy connection details, a further run of the same fixture returned the map in approximately **0.013 seconds** on repeat requests, with **144 KiB** of HTML. Its first visit, using an already-prepared source projection, took about 0.106 seconds. These timings include server-side work only; Chrome separately verified that no connection-evidence request occurs until a connection opens.

These are development measurements, not a production latency promise. The naming fixture has no applicable policy; regression tests separately exercise active policies, proposal changes, simulations and exception expiry. First-use preparation adds work for existing snapshots; the warming command avoids that work on the first user visit.

PostgreSQL 17 migration and query-plan checks used separate disposable data. The group query selected `snapshot_group_projection`; for the single-snapshot rule fixture PostgreSQL preferred a sequential scan. Production plans depend on snapshot count, row sizes and statistics. Normal cache hits avoid the source-record scans entirely.

The migration-retry, duplicate-session and progress-logging fixes were verified on PostgreSQL 17 with all 799 Django tests, including 12 recovery/observation tests. These exercise a fresh install, an unrecorded partial migration, a real cancelled concurrent build leaving an invalid index, conflicting definitions, retained snapshot data, repeated rollback/retry, and a duplicate migrator stopping without waiting or disturbing the session holding the lock. Observer checks cover repeated samples from a real writer-blocked build, blocker IDs, cancellation SQLSTATE, observer session cleanup, read-only/autocommit/timeouts, observation failure isolation and SQL-plan generation without a monitor. Fresh SQLite migrations and both backends' model checks also pass.

Chrome checks cover remote group search, retained selections, matrix generation, both appearance modes, paginated membership drawers and automatic fragment refresh. Additional map checks cover no eager connection requests, fetching on expansion, local reuse on reopening, pinned service filters and stale-evidence recovery.
